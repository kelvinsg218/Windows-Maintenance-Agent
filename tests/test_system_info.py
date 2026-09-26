import pytest

from maintenance.results import Status
from maintenance.system_info import (
    NT_CURRENT_VERSION,
    DiskUsage,
    collect_system_info,
    evaluate_disk_space,
    get_architecture,
    get_system_drive,
    get_windows_version,
    normalize_product_name,
)
from tests.conftest import windows_only

GB = 1024**3


@pytest.mark.parametrize(
    ("product", "build", "expected"),
    [
        ("Windows 10 Pro", "26100", "Windows 11 Pro"),
        ("Windows 10 Home Single Language", "26200", "Windows 11 Home Single Language"),
        ("Windows 10 Enterprise", "19045", "Windows 10 Enterprise"),
        ("Windows 10 Pro", "", "Windows 10 Pro"),
        ("Windows Server 2022 Standard", "20348", "Windows Server 2022 Standard"),
    ],
)
def test_product_name_is_fixed_for_windows_11(product, build, expected):
    assert normalize_product_name(product, build) == expected


def test_windows_version_from_registry(registry):
    registry.set("HKLM", NT_CURRENT_VERSION, "ProductName", "Windows 10 Pro")
    registry.set("HKLM", NT_CURRENT_VERSION, "DisplayVersion", "24H2")
    registry.set("HKLM", NT_CURRENT_VERSION, "CurrentBuildNumber", "26100")
    registry.set("HKLM", NT_CURRENT_VERSION, "UBR", 4061)
    version = get_windows_version(registry)
    assert version.product_name == "Windows 11 Pro"
    assert version.display_version == "24H2"
    assert version.build == "26100.4061"
    assert version.short == "Windows 11 Pro 24H2"
    assert version.full == "Windows 11 Pro 24H2 (build 26100.4061)"


def test_windows_version_falls_back_to_release_id(registry):
    registry.set("HKLM", NT_CURRENT_VERSION, "ProductName", "Windows 10 Enterprise")
    registry.set("HKLM", NT_CURRENT_VERSION, "ReleaseId", "1909")
    registry.set("HKLM", NT_CURRENT_VERSION, "CurrentBuild", "18363")
    version = get_windows_version(registry)
    assert version.display_version == "1909"
    assert version.build == "18363"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"PROCESSOR_ARCHITECTURE": "AMD64"}, "x64"),
        ({"PROCESSOR_ARCHITECTURE": "x86", "PROCESSOR_ARCHITEW6432": "AMD64"}, "x64"),
        ({"PROCESSOR_ARCHITECTURE": "ARM64"}, "ARM64"),
        ({"PROCESSOR_ARCHITECTURE": "x86"}, "x86"),
    ],
)
def test_architecture_mapping(env, expected):
    assert get_architecture(env) == expected


def test_system_drive_validation():
    assert get_system_drive({"SystemDrive": "d:"}) == "D:"
    assert get_system_drive({"SystemDrive": "C:\\..\\Windows"}) == "C:"
    assert get_system_drive({}) == "C:"


def test_disk_usage_percentages():
    disk = DiskUsage("C:", total_bytes=200 * GB, free_bytes=50 * GB)
    assert disk.used_bytes == 150 * GB
    assert disk.used_percent == pytest.approx(75.0)
    assert disk.free_percent == pytest.approx(25.0)


@pytest.mark.parametrize(
    ("free_gb", "status"),
    [(50, Status.SUCCESS), (15, Status.WARNING), (9, Status.WARNING)],
)
def test_disk_space_evaluation(free_gb, status):
    # 15 GB de 200 GB = 7.5% (< 10%); 9 GB < 10 GB mínimo absoluto
    disk = DiskUsage("C:", total_bytes=200 * GB, free_bytes=free_gb * GB)
    result = evaluate_disk_space(disk, min_free_percent=10, min_free_gb=10)
    assert result.status is status
    assert result.details["drive"] == "C:"


def test_disk_space_unknown_is_warning():
    result = evaluate_disk_space(None, min_free_percent=10, min_free_gb=10)
    assert result.status is Status.WARNING


@windows_only
def test_collect_real_system_info_smoke():
    from maintenance.registry import WinRegistryReader

    info = collect_system_info(WinRegistryReader())
    assert info.hostname
    assert info.username
    assert info.windows.product_name.startswith("Windows")
    assert info.disk is not None and info.disk.total_bytes > 0
    assert info.uptime_seconds and info.uptime_seconds > 0
    assert set(info.to_dict()) >= {"hostname", "windows", "disk", "architecture"}
