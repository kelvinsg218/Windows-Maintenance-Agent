from datetime import datetime

from maintenance.formatting import (
    format_bytes,
    format_datetime,
    format_duration,
    format_uptime,
    yes_no,
)


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(int(2.1 * 1024**3)) == "2.1 GB"
    assert format_bytes(None) == "-"


def test_format_duration_report_and_compact():
    assert format_duration(14 * 60 + 32) == "14m 32s"
    assert format_duration(45) == "45s"
    assert format_duration(3725) == "1h 02m 05s"
    assert format_duration(8 * 60 + 11, compact=True) == "08m11s"
    assert format_duration(3725, compact=True) == "1h02m05s"
    assert format_duration(None) == "-"


def test_format_uptime():
    assert format_uptime(59) == "0m"
    assert format_uptime(3 * 3600 + 5 * 60) == "3h 5m"
    assert format_uptime(2 * 86400 + 3600) == "2d 1h 0m"
    assert format_uptime(None) == "desconhecido"


def test_format_datetime_and_yes_no():
    moment = datetime(2026, 9, 26, 8, 32, 5)
    assert format_datetime(moment) == "26/09/2026 08:32"
    assert format_datetime(moment, seconds=True) == "26/09/2026 08:32:05"
    assert yes_no(True) == "Sim"
    assert yes_no(False) == "Não"
