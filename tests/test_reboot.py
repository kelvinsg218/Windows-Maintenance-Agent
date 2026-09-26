from maintenance.reboot import (
    ACTIVE_COMPUTER_NAME,
    CBS_REBOOT_PENDING,
    PENDING_COMPUTER_NAME,
    REBOOT_GENERIC_MESSAGE,
    REBOOT_UPDATES_MESSAGE,
    SESSION_MANAGER,
    SOURCE_CBS,
    SOURCE_COMPUTER_RENAME,
    SOURCE_FILE_RENAME,
    SOURCE_INSTALLATION,
    SOURCE_UPDATE_EXE,
    SOURCE_WINDOWS_UPDATE,
    UPDATES_KEY,
    WU_REBOOT_REQUIRED,
    count_pending_file_operations,
    detect_pending_reboot,
    reboot_result,
)
from maintenance.results import Status


def test_no_indicators_means_no_reboot(registry):
    status = detect_pending_reboot(registry)
    assert not status.required
    assert status.message == ""
    assert reboot_result(status).status is Status.SUCCESS


def test_cbs_and_windows_update_keys(registry):
    registry.add_key("HKLM", CBS_REBOOT_PENDING).add_key("HKLM", WU_REBOOT_REQUIRED)
    status = detect_pending_reboot(registry)
    assert status.required
    assert status.sources == (SOURCE_CBS, SOURCE_WINDOWS_UPDATE)
    assert status.update_related
    assert status.message == REBOOT_UPDATES_MESSAGE
    result = reboot_result(status)
    assert result.status is Status.WARNING
    assert result.details["reboot_required"] is True


def test_update_exe_volatile(registry):
    registry.set("HKLM", UPDATES_KEY, "UpdateExeVolatile", 1)
    assert detect_pending_reboot(registry).sources == (SOURCE_UPDATE_EXE,)
    registry.set("HKLM", UPDATES_KEY, "UpdateExeVolatile", 0)
    assert not detect_pending_reboot(registry).required


def test_pending_deletes_only_do_not_require_reboot(registry):
    entries = ["\\??\\C:\\Temp\\a.tmp", "", "\\??\\C:\\Temp\\b.tmp", ""]
    registry.set("HKLM", SESSION_MANAGER, "PendingFileRenameOperations", entries)
    status = detect_pending_reboot(registry)
    assert not status.required
    assert status.pending_file_deletes == 2
    assert "exclusões de arquivos agendadas" in reboot_result(status).summary


def test_pending_file_replacement_requires_generic_reboot(registry):
    entries = [
        "*1\\??\\C:\\Windows\\System32\\drv.dll.0",
        "",
        "*1\\??\\C:\\Windows\\System32\\New\\X.DLL",
        "*1\\??\\C:\\Windows\\System32\\X.DLL",
        "",
    ]
    registry.set("HKLM", SESSION_MANAGER, "PendingFileRenameOperations", entries)
    status = detect_pending_reboot(registry)
    assert status.required
    assert status.sources == (SOURCE_FILE_RENAME,)
    assert not status.update_related
    assert status.message == REBOOT_GENERIC_MESSAGE
    assert (status.pending_file_renames, status.pending_file_deletes) == (1, 1)


def test_computer_rename_pending(registry):
    registry.set("HKLM", ACTIVE_COMPUTER_NAME, "ComputerName", "TI-04")
    registry.set("HKLM", PENDING_COMPUTER_NAME, "ComputerName", "TI-05")
    assert detect_pending_reboot(registry).sources == (SOURCE_COMPUTER_RENAME,)
    registry.set("HKLM", PENDING_COMPUTER_NAME, "ComputerName", "ti-04")
    assert not detect_pending_reboot(registry).required


def test_count_pending_file_operations_edge_cases():
    assert count_pending_file_operations(None) == (0, 0)
    assert count_pending_file_operations([]) == (0, 0)
    assert count_pending_file_operations(["a"]) == (0, 1)
    assert count_pending_file_operations(["a", "b", "c"]) == (1, 1)
    assert count_pending_file_operations(["", "", "a", "b"]) == (1, 0)


def test_installation_reboot_is_merged(registry):
    status = detect_pending_reboot(registry).with_installation_reboot(True)
    assert status.required and status.update_related
    assert status.sources == (SOURCE_INSTALLATION,)
    assert status.with_installation_reboot(True).sources == (SOURCE_INSTALLATION,)
    unchanged = detect_pending_reboot(registry)
    assert unchanged.with_installation_reboot(False) is unchanged
