"""Histórico local das execuções de manutenção (SQLite)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
RESULT_RUNNING = "RUNNING"
RESULT_INTERRUPTED = "INTERRUPTED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds REAL,
    result TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0,
    tasks TEXT NOT NULL DEFAULT '',
    chrome_updated INTEGER,
    windows_updates_installed INTEGER NOT NULL DEFAULT 0,
    cleanup_performed INTEGER NOT NULL DEFAULT 0,
    bytes_removed INTEGER NOT NULL DEFAULT 0,
    reboot_required INTEGER NOT NULL DEFAULT 0,
    network_sync TEXT,
    agent_version TEXT NOT NULL,
    log_file TEXT,
    report_file TEXT
)
"""


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    hostname: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    result: str
    dry_run: bool
    tasks: str
    chrome_updated: bool | None
    windows_updates_installed: int
    cleanup_performed: bool
    bytes_removed: int
    reboot_required: bool
    network_sync: str | None
    agent_version: str
    log_file: str | None
    report_file: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> HistoryEntry:
        return cls(
            id=row["id"],
            hostname=row["hostname"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            duration_seconds=row["duration_seconds"],
            result=row["result"],
            dry_run=bool(row["dry_run"]),
            tasks=row["tasks"],
            chrome_updated=None if row["chrome_updated"] is None else bool(row["chrome_updated"]),
            windows_updates_installed=row["windows_updates_installed"],
            cleanup_performed=bool(row["cleanup_performed"]),
            bytes_removed=row["bytes_removed"],
            reboot_required=bool(row["reboot_required"]),
            network_sync=row["network_sync"],
            agent_version=row["agent_version"],
            log_file=row["log_file"],
            report_file=row["report_file"],
        )


class HistoryStore:
    def __init__(self, db_path: Path, *, readonly: bool = False) -> None:
        self.db_path = db_path
        self.readonly = readonly

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=10)
        else:
            connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:  # commit ou rollback
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute(_SCHEMA)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def start_run(
        self,
        *,
        hostname: str,
        started_at: datetime,
        tasks: list[str],
        dry_run: bool,
        agent_version: str,
        log_file: str | None,
    ) -> int:
        with self._session() as db:
            cursor = db.execute(
                "INSERT INTO runs (hostname, started_at, result, dry_run, tasks, agent_version,"
                " log_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    hostname,
                    started_at.isoformat(timespec="seconds"),
                    RESULT_RUNNING,
                    int(dry_run),
                    ",".join(tasks),
                    agent_version,
                    log_file,
                ),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        duration_seconds: float,
        result: str,
        chrome_updated: bool | None,
        windows_updates_installed: int,
        cleanup_performed: bool,
        bytes_removed: int,
        reboot_required: bool,
        report_file: str | None,
    ) -> None:
        with self._session() as db:
            db.execute(
                "UPDATE runs SET finished_at = ?, duration_seconds = ?, result = ?,"
                " chrome_updated = ?, windows_updates_installed = ?, cleanup_performed = ?,"
                " bytes_removed = ?, reboot_required = ?, report_file = ? WHERE id = ?",
                (
                    finished_at.isoformat(timespec="seconds"),
                    round(duration_seconds, 1),
                    result,
                    None if chrome_updated is None else int(chrome_updated),
                    windows_updates_installed,
                    int(cleanup_performed),
                    bytes_removed,
                    int(reboot_required),
                    report_file,
                    run_id,
                ),
            )

    def set_network_sync(self, run_id: int, status: str) -> None:
        with self._session() as db:
            db.execute("UPDATE runs SET network_sync = ? WHERE id = ?", (status, run_id))

    def mark_interrupted(self) -> int:
        """Execuções que ficaram RUNNING (processo encerrado) passam a INTERRUPTED."""
        with self._session() as db:
            cursor = db.execute(
                "UPDATE runs SET result = ? WHERE result = ?", (RESULT_INTERRUPTED, RESULT_RUNNING)
            )
            return cursor.rowcount

    def list_runs(self, limit: int = 10) -> list[HistoryEntry]:
        with self._session() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [HistoryEntry.from_row(row) for row in rows]

    def get_run(self, run_id: int) -> HistoryEntry | None:
        with self._session() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return HistoryEntry.from_row(row) if row else None

    def last_completed_run(self, *, include_dry_run: bool = False) -> HistoryEntry | None:
        query = "SELECT * FROM runs WHERE finished_at IS NOT NULL"
        if not include_dry_run:
            query += " AND dry_run = 0"
        with self._session() as db:
            row = db.execute(query + " ORDER BY id DESC LIMIT 1").fetchone()
        return HistoryEntry.from_row(row) if row else None


def open_history_readonly(db_path: Path) -> HistoryStore | None:
    """Abre o histórico só para leitura (comandos status/history/report não criam arquivos)."""
    return HistoryStore(db_path, readonly=True) if db_path.is_file() else None
