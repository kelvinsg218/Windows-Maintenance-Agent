"""Testes dos fluxos completos ``run`` e ``check`` em ambiente simulado."""

import sys
from dataclasses import replace

import pytest

from maintenance.config import AppConfig
from maintenance.history import RESULT_INTERRUPTED, HistoryStore
from maintenance.results import OverallResult, Status
from maintenance.runner import (
    CriticalError,
    RunLock,
    RunOptions,
    run_check,
    run_maintenance,
    select_tasks,
)
from tests.fake_env import build_environment
from tests.helpers import command_result

QUIET = RunOptions(console=False)


def statuses(outcome):
    return {result.module: result.status for result in outcome.report.modules}


def read_log(outcome):
    return outcome.log_file.read_text(encoding="utf-8")


def test_full_run_without_admin(tmp_path):
    env = build_environment(tmp_path, admin=False)
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)

    assert outcome.overall is OverallResult.SUCCESS_WITH_WARNINGS
    assert outcome.exit_code == 1
    result = statuses(outcome)
    assert result["privileges"] is Status.WARNING
    assert result["chrome"] is Status.WARNING  # instalação por máquina exige admin
    assert result["windows_update"] is Status.WARNING  # só pesquisa sem admin
    assert result["cleanup"] is Status.SUCCESS
    assert "upgrade" not in env.runner.subcommands()
    assert env.wua.installed == []
    assert not env.old_file.exists() and env.recent_file.exists()

    log = read_log(outcome)
    for line in (
        "| INFO | Maintenance started",
        "| INFO | Hostname: TI-04",
        "| INFO | Checking Winget",
        "| INFO | Checking Chrome",
        "| INFO | Checking Windows Update",
        "| SUCCESS | Cleanup finished",
    ):
        assert line in log
    assert outcome.log_file.name == "TI-04_2026-09-26_080001.log"
    assert outcome.report_file.read_text(encoding="utf-8").startswith("=" * 40)
    assert outcome.json_file.is_file()

    entry = HistoryStore(env.paths.history_db).get_run(outcome.run_id)
    assert entry.result == "SUCCESS_WITH_WARNINGS"
    assert entry.cleanup_performed and entry.bytes_removed == 4096
    assert entry.chrome_updated is False
    assert entry.windows_updates_installed == 0
    assert entry.report_file == str(outcome.report_file)


def test_chrome_failure_does_not_stop_windows_update(tmp_path):
    env = build_environment(tmp_path, admin=True, chrome_upgrade=command_result(0x8A150101))
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)
    result = statuses(outcome)
    assert result["chrome"] is Status.FAILED
    assert result["windows_update"] is Status.SUCCESS
    assert env.wua.installed == ["Cumulative (KB5065789)"]
    assert result["cleanup"] is Status.SUCCESS
    assert outcome.overall is OverallResult.SUCCESS_WITH_WARNINGS
    assert "| ERROR | Chrome update failed" in read_log(outcome)
    entry = HistoryStore(env.paths.history_db).get_run(outcome.run_id)
    assert entry.windows_updates_installed == 1


def test_only_requested_task_failing_is_failed(tmp_path):
    env = build_environment(tmp_path, admin=True, chrome_upgrade=command_result(0x8A150105))
    outcome = run_maintenance(
        env.config, env.paths, RunOptions(tasks=("chrome",), console=False), env.services
    )
    assert outcome.overall is OverallResult.FAILED
    assert outcome.exit_code == 2
    windows_update = outcome.report.module("windows_update")
    assert windows_update.status is Status.SKIPPED
    assert windows_update.summary == "Não solicitado nesta execução"
    assert env.wua_clients == []
    assert env.old_file.exists()


def test_dry_run_changes_nothing(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    env = build_environment(tmp_path, admin=True, config={"network_log_path": str(share)})
    outcome = run_maintenance(
        env.config, env.paths, RunOptions(dry_run=True, console=False), env.services
    )
    assert "upgrade" not in env.runner.subcommands()
    assert env.wua.installed == []
    assert env.old_file.exists()
    assert outcome.report.dry_run
    assert outcome.network_sync.status is Status.SKIPPED
    assert list(share.iterdir()) == []
    assert "Mode: DRY-RUN" in read_log(outcome)
    assert HistoryStore(env.paths.history_db).get_run(outcome.run_id).dry_run


def test_reboot_required_after_installation(tmp_path):
    env = build_environment(tmp_path, admin=True, wua_options={"reboot_after": True})
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)
    assert outcome.report.reboot.required
    assert outcome.overall is OverallResult.SUCCESS_WITH_WARNINGS
    log = read_log(outcome)
    assert "| WARNING | REBOOT_REQUIRED = true" in log
    assert "ATENÇÃO: existem atualizações que exigem reinicialização." in log
    report = outcome.report_file.read_text(encoding="utf-8")
    assert "REBOOT_REQUIRED = true" in report
    assert HistoryStore(env.paths.history_db).get_run(outcome.run_id).reboot_required


def test_network_sync_success(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    env = build_environment(tmp_path, admin=True, config={"network_log_path": str(share)})
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)
    assert outcome.network_sync.status is Status.SUCCESS
    copied = sorted(path.suffix for path in (share / "TI-04").iterdir())
    assert copied == [".json", ".log", ".txt"]
    assert HistoryStore(env.paths.history_db).get_run(outcome.run_id).network_sync == "SUCCESS"


def test_network_sync_failure_is_only_a_warning(tmp_path):
    blocker = tmp_path / "offline.txt"
    blocker.write_text("x", encoding="utf-8")
    env = build_environment(
        tmp_path, admin=True, config={"network_log_path": str(blocker / "maintenance")}
    )
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)
    assert outcome.network_sync.status is Status.WARNING
    assert outcome.overall is OverallResult.SUCCESS
    assert outcome.report_file.is_file() and outcome.log_file.is_file()
    assert "| WARNING | Network log synchronization failed." in read_log(outcome)
    assert HistoryStore(env.paths.history_db).get_run(outcome.run_id).network_sync == "WARNING"


def test_concurrent_run_is_blocked(tmp_path):
    env = build_environment(tmp_path)
    env.paths.ensure()
    lock = RunLock(env.paths.lock_file)
    assert lock.acquire()
    try:
        with pytest.raises(CriticalError, match="outra manutenção"):
            run_maintenance(env.config, env.paths, QUIET, env.services)
    finally:
        lock.release()
    assert run_maintenance(env.config, env.paths, QUIET, env.services).run_id == 1


def test_unexpected_module_error_is_isolated(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("bug simulado")

    monkeypatch.setattr("maintenance.runner.chrome.process_chrome", explode)
    env = build_environment(tmp_path, admin=True)
    outcome = run_maintenance(env.config, env.paths, QUIET, env.services)
    chrome = outcome.report.module("chrome")
    assert chrome.status is Status.FAILED
    assert "bug simulado" in chrome.summary
    assert env.wua.installed  # Windows Update seguiu normalmente
    assert "Unexpected error in module chrome" in read_log(outcome)


def test_previous_running_entry_is_marked_interrupted(tmp_path):
    env = build_environment(tmp_path)
    first = run_maintenance(env.config, env.paths, QUIET, env.services)
    store = HistoryStore(env.paths.history_db)
    orphan = store.start_run(
        hostname="TI-04",
        started_at=first.report.started_at,
        tasks=[],
        dry_run=False,
        agent_version="1.0.0",
        log_file=None,
    )
    run_maintenance(env.config, env.paths, QUIET, env.services)
    assert store.get_run(orphan).result == RESULT_INTERRUPTED


def test_check_is_read_only_even_as_admin(tmp_path):
    env = build_environment(tmp_path, admin=True)
    seen = []
    outcome = run_check(env.config, env.paths, services=env.services, on_result=seen.append)
    assert [result.module for result in outcome.results] == [
        "privileges",
        "winget",
        "chrome",
        "windows_update",
        "disk",
        "cleanup",
        "reboot",
    ]
    assert seen == outcome.results
    assert outcome.updates_available == 2  # Chrome + 1 do Windows
    assert outcome.recoverable_bytes == 4096
    assert outcome.exit_code == 1
    assert "upgrade" not in env.runner.subcommands()
    assert env.wua.installed == []
    assert env.old_file.exists()
    assert outcome.log_file.name.endswith("_check.log")
    assert not env.paths.history_db.exists()


def test_check_respects_task_selection(tmp_path):
    env = build_environment(tmp_path)
    outcome = run_check(env.config, env.paths, tasks=("chrome",), services=env.services)
    modules = [result.module for result in outcome.results]
    assert "windows_update" not in modules and "cleanup" not in modules
    assert outcome.recoverable_bytes is None


def test_select_tasks():
    assert select_tasks(AppConfig()) == ["chrome", "windows_update", "cleanup"]
    assert select_tasks(AppConfig(update_windows=False)) == ["chrome", "cleanup"]
    assert select_tasks(AppConfig(update_chrome=False), ["cleanup", "chrome"]) == [
        "chrome",
        "cleanup",
    ]


def test_non_windows_platform_is_critical(tmp_path, monkeypatch):
    env = build_environment(tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(CriticalError, match="apenas no Windows"):
        run_maintenance(env.config, env.paths, QUIET, env.services)
    with pytest.raises(CriticalError):
        run_check(env.config, env.paths, services=env.services)


def test_config_disabled_tasks_are_reported(tmp_path):
    env = build_environment(tmp_path)
    config = replace(env.config, update_chrome=False, cleanup_temp=False)
    outcome = run_maintenance(config, env.paths, QUIET, env.services)
    assert outcome.report.module("chrome").summary == "Desabilitado na configuração"
    assert env.old_file.exists()


def test_run_reports_progress_before_each_step_and_every_result(tmp_path):
    env = build_environment(tmp_path, admin=True)
    events = []
    outcome = run_maintenance(
        env.config,
        env.paths,
        QUIET,
        env.services,
        on_result=lambda result: events.append(("result", result.module)),
        progress=lambda message: events.append(("progress", message)),
    )
    results = [name for kind, name in events if kind == "result"]
    assert results == [result.module for result in outcome.report.modules]
    assert len(results) == 8
    messages = [message for kind, message in events if kind == "progress"]
    assert messages[0] == "Coletando informações do sistema..."
    assert messages[-1] == "Gerando relatório..."
    chrome_step = events.index(("progress", "Atualizando o Google Chrome..."))
    assert chrome_step < events.index(("result", "chrome"))
    assert not any("rede" in message for message in messages)  # sem compartilhamento


def test_progress_reflects_dry_run_and_selected_tasks(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    env = build_environment(tmp_path, config={"network_log_path": str(share)})
    messages = []
    run_maintenance(
        env.config,
        env.paths,
        RunOptions(tasks=("cleanup",), dry_run=True, console=False),
        env.services,
        progress=messages.append,
    )
    assert "Limpando arquivos temporários (simulação)..." in messages
    assert not any("Chrome" in message for message in messages)
    assert not any("rede" in message for message in messages)  # dry-run não sincroniza

    messages.clear()
    run_maintenance(
        env.config,
        env.paths,
        RunOptions(tasks=("cleanup",), console=False),
        env.services,
        progress=messages.append,
    )
    assert "Limpando arquivos temporários..." in messages
    assert messages[-1] == "Copiando log e relatórios para o compartilhamento de rede..."
