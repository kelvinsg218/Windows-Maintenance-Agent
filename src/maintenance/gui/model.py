"""Lógica de apresentação da interface gráfica (sem Tkinter, testável isoladamente).

Converte os resultados dos fluxos existentes (``status``, ``check`` e ``run``) em
textos, níveis visuais e recomendações, sem inventar informações: tudo vem dos
módulos do agente ou de APIs do Windows.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maintenance.chrome import SCOPE_MACHINE, ChromeInstallation, detect_chrome
from maintenance.config import AppConfig, AppPaths
from maintenance.formatting import format_bytes, format_datetime
from maintenance.gui import texts
from maintenance.history import HistoryEntry, open_history_readonly
from maintenance.privileges import PrivilegeInfo
from maintenance.reboot import RebootStatus, detect_pending_reboot
from maintenance.results import (
    TASK_CHROME,
    TASK_CLEANUP,
    TASK_WINDOWS_UPDATE,
    TASKS,
    ModuleResult,
    OverallResult,
    Status,
)
from maintenance.runner import CheckOutcome, Services
from maintenance.system_info import (
    MemoryStatus,
    SystemInfo,
    evaluate_disk_space,
    get_memory_status,
    get_processor_name,
)
from maintenance.winget import WingetInfo, get_winget_info

MAX_LISTED_ITEMS = 8

_STATUS_LEVELS: dict[Status, str] = {
    Status.SUCCESS: "ok",
    Status.WARNING: "warning",
    Status.FAILED: "error",
    Status.SKIPPED: "skipped",
}

# Ordem das etapas do "check" (ver runner.run_check) e mensagem exibida durante cada uma.
CHECK_ORDER: tuple[str, ...] = (
    "privileges",
    "winget",
    TASK_CHROME,
    TASK_WINDOWS_UPDATE,
    "disk",
    TASK_CLEANUP,
    "reboot",
)
CHECK_STEP_MESSAGES: dict[str, str] = {
    "privileges": "Verificando privilégios de administrador...",
    "winget": "Verificando o gerenciador de pacotes (winget)...",
    TASK_CHROME: "Procurando atualizações do Google Chrome...",
    TASK_WINDOWS_UPDATE: "Pesquisando atualizações do Windows (pode levar alguns minutos)...",
    "disk": "Verificando o espaço em disco...",
    TASK_CLEANUP: "Estimando arquivos temporários removíveis...",
    "reboot": "Verificando reinicialização pendente...",
}
# O "run" registra sempre 8 resultados (ver runner._run_locked), mais a etapa do relatório.
RUN_RESULT_COUNT = 8


def check_steps(selected: Sequence[str]) -> list[str]:
    """Etapas que o ``check`` executará para as tarefas selecionadas."""
    return [step for step in CHECK_ORDER if step not in TASKS or step in selected]


# --- Resultados -------------------------------------------------------------------------


@dataclass(frozen=True)
class Presentation:
    level: str
    badge: str
    title: str
    text: str
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def result_level(result: ModuleResult) -> str:
    if result.module == "privileges" and result.status is Status.WARNING:
        return "info"  # não é um problema do computador, apenas o modo de execução
    return _STATUS_LEVELS[result.status]


def _limited(items: list[str]) -> list[str]:
    if len(items) <= MAX_LISTED_ITEMS:
        return items
    return items[:MAX_LISTED_ITEMS] + [f"... e mais {len(items) - MAX_LISTED_ITEMS}"]


def _windows_update_details(details: dict[str, Any]) -> list[str]:
    installed = [str(title) for title in details.get("installed") or []]
    if installed:
        return _limited([f"Instalada: {title}" for title in installed])
    updates = details.get("updates") or []
    pending = [str(u.get("title")) for u in updates if u.get("eligible")]
    manual = [str(u.get("title")) for u in updates if u.get("manual_action")]
    lines = [f"Pendente: {title}" for title in pending]
    lines += [f"Requer ação manual: {title}" for title in manual]
    return _limited(lines)


def _cleanup_details(details: dict[str, Any], *, applied: bool) -> list[str]:
    if "files_scanned" not in details:
        return list(details.get("notes", [])) + list(details.get("warnings", []))
    scanned = details.get("files_scanned", 0)
    if applied and not details.get("dry_run"):
        line = (
            f"{details.get('files_removed', 0)} de {scanned} arquivos analisados foram "
            f"removidos ({format_bytes(details.get('bytes_removed', 0))}); "
            f"{details.get('files_skipped', 0)} preservados (recentes ou em uso)"
        )
    else:
        line = (
            f"{details.get('files_eligible', 0)} de {scanned} arquivos analisados podem ser "
            f"removidos ({format_bytes(details.get('bytes_eligible', 0))})"
        )
    notes = list(details.get("notes", [])) + list(details.get("warnings", []))
    return [line, *notes]


def present_result(result: ModuleResult, *, applied: bool = False) -> Presentation:
    """Título, texto e detalhes de um resultado. ``applied`` = resultado de uma manutenção."""
    level = result_level(result)
    badge = texts.LEVEL_LABELS[level]
    if result.status is Status.WARNING and result.updates_available:
        badge = texts.UPDATE_AVAILABLE
    title = texts.MODULE_TITLES.get(result.module, result.module)
    text = texts.display_text(result.summary)
    details: list[str] = []
    if result.module == "privileges":
        text = (
            texts.PRIVILEGES_ADMIN if result.details.get("is_admin") else texts.PRIVILEGES_LIMITED
        )
    elif result.module == "reboot" and result.details.get("reboot_required"):
        sources = texts.reboot_sources(result.details.get("sources", []))
        text = (
            f"Reinicialização necessária ({sources}). "
            "O aplicativo nunca reinicia o computador automaticamente."
        )
    elif result.module == TASK_WINDOWS_UPDATE:
        details = _windows_update_details(result.details)
    elif result.module == TASK_CLEANUP:
        if applied:
            title = "Limpeza de arquivos temporários"
        details = _cleanup_details(result.details, applied=applied)
    errors = [texts.display_text(error) for error in result.errors]
    return Presentation(level, badge, title, text, details, errors)


def visible_results(
    results: Sequence[ModuleResult], requested: Sequence[str]
) -> list[ModuleResult]:
    """Resultados exibidos ao usuário: sem a coleta interna e sem rotinas não solicitadas."""
    return [
        result
        for result in results
        if result.module != "system_info"
        and (result.module not in TASKS or result.module in requested)
    ]


# --- Recomendações da verificação ---------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    level: str
    text: str
    task: str | None = None  # rotina da Manutenção que resolve a pendência
    needs_admin: bool = False


def recommend(result: ModuleResult, *, is_admin: bool) -> Recommendation | None:
    """Recomendação para um resultado do ``check`` (``None`` = nada a fazer)."""
    module, status, details = result.module, result.status, result.details
    if module == "privileges":
        if is_admin or status is not Status.WARNING:
            return None
        return Recommendation(
            "info",
            "Algumas correções exigem privilégios de administrador. Use “Reiniciar como "
            "administrador” antes de executar a manutenção.",
        )
    if module == "winget" and status is Status.WARNING:
        return Recommendation(
            "warning",
            "Instale ou atualize o “Instalador de Aplicativo” (App Installer) pela Microsoft "
            "Store para permitir a atualização do Google Chrome.",
        )
    if module == TASK_CHROME:
        if result.updates_available:
            needs_admin = details.get("scope") == SCOPE_MACHINE
            return Recommendation(
                "warning",
                "Há uma nova versão do Google Chrome. Atualize-o pela Manutenção.",
                TASK_CHROME,
                needs_admin and not is_admin,
            )
        if status is Status.FAILED:
            return Recommendation(
                "error", "Não foi possível consultar o Google Chrome. Tente novamente mais tarde."
            )
        if status is Status.WARNING:
            return Recommendation(
                "warning",
                "Verifique as atualizações pelo próprio navegador (Configurações > Sobre o "
                "Google Chrome).",
            )
        return None
    if module == TASK_WINDOWS_UPDATE:
        if result.updates_available:
            return Recommendation(
                "warning",
                "Instale as atualizações pendentes do Windows pela Manutenção.",
                TASK_WINDOWS_UPDATE,
                not is_admin,
            )
        if details.get("manual_action"):
            return Recommendation(
                "warning",
                "Algumas atualizações exigem ação manual: abra Configurações > Windows Update.",
            )
        if status is Status.FAILED:
            return Recommendation(
                "error",
                "A pesquisa do Windows Update falhou. Verifique a conexão com a internet e "
                "tente novamente.",
            )
        return None
    if module == TASK_CLEANUP:
        eligible = int(details.get("bytes_eligible") or 0)
        if eligible > 0:
            return Recommendation(
                "warning" if status is Status.WARNING else "info",
                f"É possível liberar até {format_bytes(eligible)} com a limpeza de arquivos "
                "temporários.",
                TASK_CLEANUP,
            )
        if status is Status.WARNING:
            return Recommendation("warning", texts.display_text(result.summary))
        return None
    if module == "disk" and status is Status.WARNING:
        return Recommendation(
            "warning",
            "Pouco espaço livre: remova arquivos e programas que não usa e execute a limpeza "
            "de arquivos temporários.",
            TASK_CLEANUP,
        )
    if module == "reboot" and status is Status.WARNING:
        return Recommendation(
            "warning",
            "Reinicie o computador quando for conveniente para concluir as alterações "
            "pendentes. O aplicativo nunca reinicia automaticamente.",
        )
    if status is Status.FAILED:
        title = texts.MODULE_TITLES.get(module, module)
        return Recommendation("error", f"A verificação de “{title}” falhou. Consulte o log.")
    return None


@dataclass(frozen=True)
class CheckSummary:
    level: str
    title: str
    text: str
    problems: int


def summarize_check(outcome: CheckOutcome) -> CheckSummary:
    problems = [
        result for result in outcome.results if result_level(result) in ("warning", "error")
    ]
    facts = [f"Atualizações disponíveis: {outcome.updates_available}"]
    recoverable = outcome.recoverable_bytes
    if recoverable is not None:
        facts.append(f"Espaço recuperável estimado: {format_bytes(recoverable)}")
    text = " · ".join(facts)
    if any(result.status is Status.FAILED for result in outcome.results):
        return CheckSummary("error", "Alguma verificação falhou", text, len(problems))
    if problems:
        title = texts.plural(
            len(problems), "ponto de atenção encontrado", "pontos de atenção encontrados"
        )
        return CheckSummary("warning", title, text, len(problems))
    return CheckSummary("ok", "Nenhum problema encontrado", text, 0)


# --- Painel -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardData:
    info: SystemInfo
    processor: str | None
    logical_processors: int | None
    memory: MemoryStatus | None
    privileges: PrivilegeInfo
    winget: WingetInfo | None
    chrome: ChromeInstallation
    reboot: RebootStatus
    disk: ModuleResult
    last_run: HistoryEntry | None
    history_error: str | None = None


def collect_dashboard(
    services: Services,
    config: AppConfig,
    paths: AppPaths,
    *,
    memory_status: Callable[[], MemoryStatus | None] = get_memory_status,
    cpu_count: Callable[[], int | None] = os.cpu_count,
) -> DashboardData:
    """Mesmas consultas do comando ``status`` (somente leitura), mais processador e memória."""
    info = services.collect_system_info(services.registry)
    last_run: HistoryEntry | None = None
    history_error: str | None = None
    try:
        history = open_history_readonly(paths.history_db)
        last_run = history.last_completed_run() if history else None
    except (sqlite3.Error, OSError, ValueError) as exc:
        history_error = str(exc)
    return DashboardData(
        info=info,
        processor=get_processor_name(services.registry),
        logical_processors=cpu_count(),
        memory=memory_status(),
        privileges=services.get_privileges(),
        winget=get_winget_info(services.find_winget(), runner=services.run_command),
        chrome=detect_chrome(services.registry),
        reboot=detect_pending_reboot(services.registry),
        disk=evaluate_disk_space(
            info.disk,
            min_free_percent=config.disk_min_free_percent,
            min_free_gb=config.disk_min_free_gb,
        ),
        last_run=last_run,
        history_error=history_error,
    )


@dataclass(frozen=True)
class Assessment:
    level: str
    title: str
    items: list[tuple[str, str]]  # (nível, texto)


def assess_dashboard(data: DashboardData | None, *, config_error: str | None = None) -> Assessment:
    """Status geral a partir das verificações rápidas do painel."""
    items: list[tuple[str, str]] = []
    if config_error:
        items.append(("error", f"Erro no arquivo de configuração: {config_error}"))
    if data is not None:
        if data.reboot.required:
            items.append(
                (
                    "warning",
                    f"Reinicialização pendente ({texts.reboot_sources(data.reboot.sources)}). "
                    "Reinicie o computador quando for conveniente.",
                )
            )
        if data.disk.status is Status.WARNING:
            items.append(("warning", texts.display_text(data.disk.summary) + "."))
        if data.winget is None:
            items.append(
                (
                    "warning",
                    "Winget não encontrado: não é possível atualizar o Google Chrome pelo "
                    "aplicativo.",
                )
            )
        if data.last_run is not None and data.last_run.result == OverallResult.FAILED.value:
            items.append(
                ("warning", "A última manutenção terminou com falha. Consulte o Histórico.")
            )
        if data.history_error:
            items.append(("warning", "Não foi possível ler o histórico de manutenções."))
    if any(level == "error" for level, _text in items):
        return Assessment("error", "Erro crítico", items)
    if items:
        title = texts.plural(len(items), "ponto de atenção", "pontos de atenção")
        return Assessment("warning", title, items)
    return Assessment("ok", "Nenhum problema detectado", items)


def last_run_label(entry: HistoryEntry | None) -> str:
    if entry is None:
        return "Nenhuma registrada"
    label, _level = texts.history_result(entry.result)
    return f"{format_datetime(entry.started_at)} · {label}"


# --- Histórico --------------------------------------------------------------------------


def load_report(entry: HistoryEntry) -> dict[str, Any] | None:
    """Relatório JSON de uma execução (gravado ao lado do .txt), se ainda existir."""
    if not entry.report_file:
        return None
    path = Path(entry.report_file).with_suffix(".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def report_modules(data: dict[str, Any]) -> list[ModuleResult]:
    modules: list[ModuleResult] = []
    for item in data.get("modules") or []:
        if not isinstance(item, dict):
            continue
        try:
            status = Status(item.get("status"))
        except ValueError:
            continue
        modules.append(
            ModuleResult(
                str(item.get("module", "")),
                status,
                str(item.get("summary", "")),
                dict(item.get("details") or {}),
                [str(error) for error in item.get("errors") or []],
            )
        )
    return modules
