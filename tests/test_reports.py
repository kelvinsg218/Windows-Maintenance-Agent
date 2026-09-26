import json
from datetime import datetime

from maintenance.reboot import REBOOT_UPDATES_MESSAGE, RebootStatus
from maintenance.reports import MaintenanceReport, render_text, write_report
from maintenance.results import ModuleResult, OverallResult, Status
from maintenance.system_info import DiskUsage

GB = 1024**3


def sample_report(**overrides) -> MaintenanceReport:
    modules = [
        ModuleResult("privileges", Status.SUCCESS, "Executando com privilégios administrativos"),
        ModuleResult("chrome", Status.SUCCESS, "Atualizado de 153.0 para 154.0"),
        ModuleResult(
            "windows_update",
            Status.SUCCESS,
            "3 atualizações instaladas",
            {"installed": ["KB1 Cumulativa", "KB2 .NET", "KB3 Defender"]},
        ),
        ModuleResult("reboot", Status.WARNING, "Reinicialização necessária (Windows Update)"),
        ModuleResult("disk", Status.SUCCESS, "Espaço em disco suficiente"),
        ModuleResult(
            "cleanup",
            Status.SUCCESS,
            "2.0 GB removidos",
            {
                "files_scanned": 15230,
                "files_removed": 14890,
                "bytes_removed": 2 * GB,
                "files_skipped": 340,
                "files_eligible": 14890,
                "bytes_eligible": 2 * GB,
                "dry_run": False,
                "notes": [],
                "warnings": [],
            },
        ),
    ]
    values = {
        "agent_version": "1.0.0",
        "hostname": "TI-04",
        "username": "Kelvin",
        "windows": "Windows 11 Pro 24H2 (build 26100.4061)",
        "admin_label": "Sim",
        "started_at": datetime(2026, 9, 26, 8, 0, 1),
        "finished_at": datetime(2026, 9, 26, 8, 14, 33),
        "overall": OverallResult.SUCCESS_WITH_WARNINGS,
        "modules": modules,
        "requested_tasks": ["chrome", "windows_update", "cleanup"],
        "reboot": RebootStatus(True, ("Windows Update",), update_related=True),
        "disk_before": DiskUsage("C:", 256 * GB, 138 * GB),
        "disk_after": DiskUsage("C:", 256 * GB, 140 * GB),
        "run_id": 12,
        "log_file": r"logs\TI-04_2026-09-26_080001.log",
    }
    values.update(overrides)
    return MaintenanceReport(**values)


def test_text_report_follows_expected_layout():
    text = render_text(sample_report())
    for expected in (
        "WINDOWS MAINTENANCE REPORT",
        "PC: TI-04",
        "Windows: Windows 11 Pro 24H2 (build 26100.4061)",
        "Administrador: Sim",
        "Chrome\n[OK] Atualizado de 153.0 para 154.0",
        "Windows Update\n[OK] 3 atualizações instaladas\n  - KB1 Cumulativa",
        "Reinicialização\n[WARNING] Necessária (Windows Update)",
        REBOOT_UPDATES_MESSAGE,
        "REBOOT_REQUIRED = true",
        "Disco C:\nAntes: 138.0 GB livres\nDepois: 140.0 GB livres",
        "Limpeza\n[OK] 2.0 GB removidos",
        "Arquivos analisados: 15230",
        "Arquivos removidos: 14890",
        "Espaço recuperado: 2.0 GB",
        "Arquivos ignorados: 340",
        "Duração: 14m 32s",
        "Resultado:\nSUCESSO COM PENDÊNCIAS",
    ):
        assert expected in text, expected
    assert "DRY-RUN" not in text
    assert "Observações" not in text


def test_dry_run_and_observations_are_shown():
    report = sample_report(
        dry_run=True,
        reboot=RebootStatus(False),
        modules=[
            ModuleResult("privileges", Status.WARNING, "Executando sem privilégios"),
            ModuleResult("winget", Status.WARNING, "Winget não encontrado"),
            ModuleResult("chrome", Status.FAILED, "Falha ao atualizar", errors=["0x8A150101"]),
        ],
        overall=OverallResult.SUCCESS_WITH_WARNINGS,
    )
    text = render_text(report)
    assert "Modo: DRY-RUN" in text
    assert "[OK] Não necessária" in text
    assert (
        "Observações\n[WARNING] Executando sem privilégios\n[WARNING] Winget não encontrado" in text
    )
    assert "[ERROR] chrome: 0x8A150101" in text
    assert "Windows Update\n[SKIPPED] Não executado" in text


def test_json_report_is_structured(tmp_path):
    report = sample_report()
    text_path, json_path = write_report(report, tmp_path / "reports", "TI-04_2026-09-26_080001")
    assert text_path.read_text(encoding="utf-8").startswith("=" * 40)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["result"] == "SUCCESS_WITH_WARNINGS"
    assert data["run_id"] == 12
    assert data["reboot"]["reboot_required"] is True
    assert data["duration_seconds"] == 872.0
    assert data["modules"][1]["module"] == "chrome"
    assert data["started_at"] == "2026-09-26T08:00:01"
