import logging
import os
import re
import time
from datetime import datetime

import pytest

from maintenance.logging_service import (
    SUCCESS,
    close_logging,
    configure_logging,
    purge_old_files,
    redact,
    run_file_stem,
    safe_name,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    close_logging()


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=Hunter2", "Hunter2"),
        ("senha: minhaSenha123", "minhaSenha123"),
        ("token = 'abc.def.ghi'", "abc.def.ghi"),
        ('api_key="XYZ987"', "XYZ987"),
        ("Authorization: Bearer eyJhbGciOi", "eyJhbGciOi"),
        ("https://user:p4ss@server/share", "p4ss"),
    ],
)
def test_secrets_are_redacted(text, secret):
    assert secret not in redact(text)
    assert "***" in redact(text)


def test_regular_text_is_untouched():
    text = "Checking Windows Update (3 updates found) on TI-04"
    assert redact(text) == text


def test_log_file_format_levels_and_redaction(tmp_path):
    log_file = tmp_path / "logs" / "TI-04_2026-09-26_080001.log"
    logger = configure_logging(log_file, console=False)
    logger.info("Maintenance started")
    logging.getLogger("maintenance.chrome").log(SUCCESS, "Chrome updated")
    logger.warning("Network log synchronization failed. password=segredo")
    logger.debug("debug message hidden without verbose")
    close_logging()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| (INFO|SUCCESS|WARNING) \| ")
    assert all(pattern.match(line) for line in lines)
    assert lines[0].endswith("| INFO | Maintenance started")
    assert lines[1].endswith("| SUCCESS | Chrome updated")
    assert "segredo" not in lines[2] and "password=***" in lines[2]
    assert len(lines) == 3
    os.remove(log_file)  # arquivo liberado após close_logging (importante no Windows)


def test_verbose_includes_debug(tmp_path):
    log_file = tmp_path / "verbose.log"
    configure_logging(log_file, console=False, verbose=True).debug("detalhe")
    close_logging()
    assert "| DEBUG | detalhe" in log_file.read_text(encoding="utf-8")


def test_file_names():
    moment = datetime(2026, 9, 26, 8, 0, 1)
    assert run_file_stem("TI-04", moment) == "TI-04_2026-09-26_080001"
    assert run_file_stem("TI-04", moment, "_check") == "TI-04_2026-09-26_080001_check"
    assert safe_name("PC com espaço/..\\x") == "PC_com_espa_o_.._x"
    assert safe_name("...") == "UNKNOWN"


def test_purge_old_files_only_removes_old_own_files(tmp_path):
    now = time.time()
    old_log = tmp_path / "old.log"
    old_report = tmp_path / "old.json"
    new_log = tmp_path / "new.log"
    keep = tmp_path / ".gitkeep"
    other = tmp_path / "old.exe"
    for path in (old_log, old_report, new_log, keep, other):
        path.write_text("x", encoding="utf-8")
    for path in (old_log, old_report, keep, other):
        os.utime(path, (now - 100 * 86400, now - 100 * 86400))
    assert purge_old_files(tmp_path, retention_days=90, now=now) == 2
    assert not old_log.exists() and not old_report.exists()
    assert new_log.exists() and keep.exists() and other.exists()
    assert purge_old_files(tmp_path, retention_days=0, now=now) == 0
    assert purge_old_files(tmp_path / "missing", retention_days=90) == 0
