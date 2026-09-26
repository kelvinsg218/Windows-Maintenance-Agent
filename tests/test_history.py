import sqlite3
from datetime import datetime

import pytest

from maintenance.history import (
    RESULT_INTERRUPTED,
    RESULT_RUNNING,
    HistoryStore,
    open_history_readonly,
)


def start(store: HistoryStore, day: int, *, dry_run: bool = False) -> int:
    return store.start_run(
        hostname="TI-04",
        started_at=datetime(2026, 9, day, 8, 0),
        tasks=["chrome", "windows_update", "cleanup"],
        dry_run=dry_run,
        agent_version="1.0.0",
        log_file="logs/x.log",
    )


def finish(store: HistoryStore, run_id: int, day: int, result: str = "SUCCESS") -> None:
    store.finish_run(
        run_id,
        finished_at=datetime(2026, 9, day, 8, 14, 32),
        duration_seconds=872.4,
        result=result,
        chrome_updated=True,
        windows_updates_installed=3,
        cleanup_performed=True,
        bytes_removed=2 * 1024**3,
        reboot_required=True,
        report_file="reports/x.txt",
    )


@pytest.fixture
def store(tmp_path) -> HistoryStore:
    history = HistoryStore(tmp_path / "data" / "history.db")
    history.initialize()
    return history


def test_run_lifecycle(store):
    run_id = start(store, 26)
    assert store.get_run(run_id).result == RESULT_RUNNING
    finish(store, run_id, 26, "SUCCESS_WITH_WARNINGS")
    store.set_network_sync(run_id, "WARNING")
    entry = store.get_run(run_id)
    assert entry.result == "SUCCESS_WITH_WARNINGS"
    assert entry.duration_seconds == 872.4
    assert entry.chrome_updated is True
    assert entry.windows_updates_installed == 3
    assert entry.cleanup_performed and entry.reboot_required
    assert entry.bytes_removed == 2 * 1024**3
    assert entry.network_sync == "WARNING"
    assert entry.tasks == "chrome,windows_update,cleanup"
    assert entry.finished_at == datetime(2026, 9, 26, 8, 14, 32)


def test_list_is_newest_first_and_limited(store):
    for day in (12, 19, 26):
        finish(store, start(store, day), day)
    runs = store.list_runs(limit=2)
    assert [run.started_at.day for run in runs] == [26, 19]
    assert store.get_run(999) is None


def test_last_completed_run_ignores_dry_run_and_running(store):
    finish(store, start(store, 12), 12)
    finish(store, start(store, 19, dry_run=True), 19)
    start(store, 26)  # ainda em execução
    assert store.last_completed_run().started_at.day == 12
    assert store.last_completed_run(include_dry_run=True).started_at.day == 19


def test_mark_interrupted(store):
    run_id = start(store, 26)
    assert store.mark_interrupted() == 1
    assert store.get_run(run_id).result == RESULT_INTERRUPTED


def test_readonly_access(store, tmp_path):
    finish(store, start(store, 26), 26)
    readonly = open_history_readonly(store.db_path)
    assert readonly is not None
    assert readonly.list_runs()[0].result == "SUCCESS"
    with pytest.raises(sqlite3.OperationalError):
        readonly.mark_interrupted()
    assert open_history_readonly(tmp_path / "missing.db") is None


def test_schema_version_is_recorded(store):
    connection = sqlite3.connect(store.db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        connection.close()
