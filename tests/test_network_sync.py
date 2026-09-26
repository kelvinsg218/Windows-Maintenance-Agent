import time

from maintenance.network_sync import sync_files
from maintenance.results import Status


def local_files(tmp_path):
    log = tmp_path / "logs" / "TI-04_2026-09-26_080001.log"
    report = tmp_path / "reports" / "TI-04_2026-09-26_080001.txt"
    for path in (log, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("conteúdo", encoding="utf-8")
    return [log, report]


def test_not_configured_is_skipped(tmp_path):
    result = sync_files("", "TI-04", local_files(tmp_path), timeout=5)
    assert result.status is Status.SKIPPED


def test_files_are_copied_to_host_folder(tmp_path):
    files = local_files(tmp_path)
    share = tmp_path / "share" / "maintenance"
    share.mkdir(parents=True)
    result = sync_files(str(share), "TI-04", [*files, None], timeout=10)
    assert result.status is Status.SUCCESS
    assert sorted(p.name for p in (share / "TI-04").iterdir()) == sorted(f.name for f in files)
    assert result.details["files"] == [f.name for f in files]


def test_unavailable_destination_is_warning_and_keeps_local_files(tmp_path):
    files = local_files(tmp_path)
    blocker = tmp_path / "not-a-folder.txt"
    blocker.write_text("x", encoding="utf-8")
    result = sync_files(str(blocker / "maintenance"), "TI-04", files, timeout=10)
    assert result.status is Status.WARNING
    assert "Arquivos mantidos localmente" in result.summary
    assert all(path.exists() for path in files)


def test_offline_server_does_not_block(tmp_path):
    """Servidor inexistente: o timeout limita a espera mesmo se o SMB demorar a falhar."""
    started = time.monotonic()
    result = sync_files(
        r"\\wma-offline-server.invalid\TI", "TI-04", local_files(tmp_path), timeout=5
    )
    assert result.status is Status.WARNING
    assert time.monotonic() - started < 15


def test_slow_share_times_out(tmp_path):
    share = tmp_path / "share"
    share.mkdir()

    def slow_copy(source, destination):
        time.sleep(3)

    started = time.monotonic()
    result = sync_files(str(share), "TI-04", local_files(tmp_path), timeout=0.5, copy=slow_copy)
    assert result.status is Status.WARNING
    assert "tempo limite" in result.summary
    assert time.monotonic() - started < 2.5


def test_hostname_is_sanitized(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    result = sync_files(str(share), "..\\..\\evil", local_files(tmp_path), timeout=10)
    assert result.status is Status.SUCCESS
    assert [p.name for p in share.iterdir()] == ["evil"]
