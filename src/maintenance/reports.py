"""Relatório de manutenção: texto para o técnico e JSON estruturado para integrações."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from maintenance.formatting import format_bytes, format_datetime, format_duration
from maintenance.reboot import RebootStatus
from maintenance.results import (
    OVERALL_LABELS_PT,
    TASK_CHROME,
    TASK_CLEANUP,
    TASK_WINDOWS_UPDATE,
    ModuleResult,
    OverallResult,
    Status,
)
from maintenance.system_info import DiskUsage

REPORT_SCHEMA_VERSION = 1
SEPARATOR = "=" * 40
MAX_LISTED_UPDATES = 15
_SECTION_MODULES = {TASK_CHROME, TASK_WINDOWS_UPDATE, TASK_CLEANUP, "reboot", "disk"}


@dataclass
class MaintenanceReport:
    agent_version: str
    hostname: str
    username: str
    windows: str
    admin_label: str
    started_at: datetime
    finished_at: datetime
    overall: OverallResult
    modules: list[ModuleResult] = field(default_factory=list)
    requested_tasks: list[str] = field(default_factory=list)
    dry_run: bool = False
    reboot: RebootStatus | None = None
    disk_before: DiskUsage | None = None
    disk_after: DiskUsage | None = None
    run_id: int | None = None
    log_file: str | None = None
    critical_error: str | None = None

    @property
    def duration_seconds(self) -> float:
        return max((self.finished_at - self.started_at).total_seconds(), 0.0)

    def module(self, name: str) -> ModuleResult | None:
        return next((result for result in self.modules if result.module == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "agent_version": self.agent_version,
            "run_id": self.run_id,
            "hostname": self.hostname,
            "username": self.username,
            "windows": self.windows,
            "administrator": self.admin_label,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds"),
            "duration_seconds": round(self.duration_seconds, 1),
            "dry_run": self.dry_run,
            "requested_tasks": list(self.requested_tasks),
            "result": str(self.overall),
            "critical_error": self.critical_error,
            "reboot": self.reboot.to_dict() if self.reboot else None,
            "disk_before": self.disk_before.to_dict() if self.disk_before else None,
            "disk_after": self.disk_after.to_dict() if self.disk_after else None,
            "modules": [result.to_dict() for result in self.modules],
            "log_file": self.log_file,
        }


def _status_line(result: ModuleResult | None, fallback: str = "Não executado") -> str:
    if result is None:
        return f"[SKIPPED] {fallback}"
    return f"[{result.tag}] {result.summary}"


def _windows_update_lines(result: ModuleResult | None) -> list[str]:
    lines = [_status_line(result)]
    if result is None:
        return lines
    installed = result.details.get("installed") or []
    pending = [
        update["title"] for update in result.details.get("updates", []) if update.get("eligible")
    ]
    listed = installed if installed else pending
    lines += [f"  - {title}" for title in listed[:MAX_LISTED_UPDATES]]
    if len(listed) > MAX_LISTED_UPDATES:
        lines.append(f"  ... e mais {len(listed) - MAX_LISTED_UPDATES}")
    lines += [f"  ! {error}" for error in result.errors]
    return lines


def _reboot_lines(reboot: RebootStatus | None) -> list[str]:
    if reboot is None:
        return ["[SKIPPED] Não verificado"]
    if not reboot.required:
        return ["[OK] Não necessária"]
    return [
        f"[WARNING] Necessária ({', '.join(reboot.sources)})",
        reboot.message,
        "REBOOT_REQUIRED = true",
    ]


def _disk_lines(report: MaintenanceReport) -> list[str]:
    before, after = report.disk_before, report.disk_after
    drive = (before or after).drive if (before or after) else "C:"
    lines = [f"Disco {drive}"]
    if before:
        lines.append(f"Antes: {format_bytes(before.free_bytes)} livres")
    if after:
        lines.append(
            f"Depois: {format_bytes(after.free_bytes)} livres "
            f"({after.free_percent:.0f}% livre de {format_bytes(after.total_bytes)})"
        )
    disk = report.module("disk")
    if disk and disk.status is not Status.SUCCESS:
        lines.append(_status_line(disk))
    return lines


def _cleanup_lines(result: ModuleResult | None) -> list[str]:
    lines = [_status_line(result)]
    if result is None or "files_scanned" not in result.details:
        return lines
    details = result.details
    lines.append(f"Arquivos analisados: {details['files_scanned']}")
    if details.get("dry_run"):
        lines.append(
            f"Arquivos que seriam removidos: {details['files_eligible']} "
            f"({format_bytes(details['bytes_eligible'])})"
        )
    else:
        lines.append(f"Arquivos removidos: {details['files_removed']}")
        lines.append(f"Espaço recuperado: {format_bytes(details['bytes_removed'])}")
    lines.append(f"Arquivos ignorados: {details['files_skipped']}")
    lines += [f"  - {note}" for note in details.get("notes", []) + details.get("warnings", [])]
    return lines


def _observations(report: MaintenanceReport) -> list[str]:
    lines = [
        _status_line(result)
        for result in report.modules
        if result.module not in _SECTION_MODULES
        and result.status in (Status.WARNING, Status.FAILED)
    ]
    for result in report.modules:
        if result.module in (TASK_CHROME, TASK_CLEANUP):
            lines += [f"[ERROR] {result.module}: {error}" for error in result.errors]
    return lines


def render_text(report: MaintenanceReport) -> str:
    lines = [SEPARATOR, "WINDOWS MAINTENANCE REPORT", SEPARATOR, ""]
    execution = f"#{report.run_id} | " if report.run_id else ""
    lines.append(
        f"Execução: {execution}{format_datetime(report.started_at, seconds=True)} até "
        f"{format_datetime(report.finished_at, seconds=True)}"
    )
    if report.dry_run:
        lines.append("Modo: DRY-RUN (simulação: nenhuma alteração foi realizada)")
    lines += [
        f"Agent: v{report.agent_version}",
        "",
        f"PC: {report.hostname}",
        f"Usuário: {report.username}",
        f"Windows: {report.windows}",
        f"Administrador: {report.admin_label}",
        "",
    ]
    if report.critical_error:
        lines += [f"[ERROR] Erro crítico: {report.critical_error}", ""]
    lines += ["Chrome", _status_line(report.module(TASK_CHROME)), ""]
    lines += ["Windows Update", *_windows_update_lines(report.module(TASK_WINDOWS_UPDATE)), ""]
    lines += ["Reinicialização", *_reboot_lines(report.reboot), ""]
    lines += [*_disk_lines(report), ""]
    lines += ["Limpeza", *_cleanup_lines(report.module(TASK_CLEANUP)), ""]
    observations = _observations(report)
    if observations:
        lines += ["Observações", *observations, ""]
    lines += [f"Duração: {format_duration(report.duration_seconds)}", ""]
    if report.log_file:
        lines += [f"Log: {report.log_file}", ""]
    lines += ["Resultado:", OVERALL_LABELS_PT[report.overall], ""]
    return "\n".join(lines)


def write_report(report: MaintenanceReport, reports_dir: Path, stem: str) -> tuple[Path, Path]:
    """Grava ``<stem>.txt`` e ``<stem>.json`` em ``reports_dir``."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    text_path = reports_dir / f"{stem}.txt"
    json_path = reports_dir / f"{stem}.json"
    text_path.write_text(render_text(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return text_path, json_path
