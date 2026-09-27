from pathlib import Path

import pytest

from maintenance.chrome import (
    APP_PATHS_KEY,
    BEACON_KEY,
    SCOPE_MACHINE,
    SCOPE_USER,
    UNINSTALL_KEYS,
    detect_chrome,
    process_chrome,
)
from maintenance.results import Status
from maintenance.winget import (
    NO_APPLICATIONS_FOUND,
    REBOOT_REQUIRED_TO_FINISH,
    UPDATE_NOT_APPLICABLE,
)
from tests.conftest import FakeRegistry
from tests.helpers import FakeRunner, command_result, winget_table

WINGET = Path(r"C:\Users\ti\AppData\Local\Microsoft\WindowsApps\winget.exe")
NOT_FOUND = command_result(NO_APPLICATIONS_FOUND, "Nenhum pacote instalado foi encontrado.\r\n")


def listing(package_id: str, version: str, available: str = "") -> object:
    """Saída de ``winget list --source winget`` (sem coluna Origem, como na saída real)."""
    if available:
        headers, widths = ["Nome", "ID", "Versão", "Disponível"], [14, 18, 14, 13]
        row = ["Google Chrome", package_id, version, available]
    else:
        headers, widths = ["Nome", "ID", "Versão"], [14, 18, 13]
        row = ["Google Chrome", package_id, version]
    return command_result(0, winget_table(headers, widths, [row]))


@pytest.fixture
def chrome_exe(tmp_path) -> Path:
    exe = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def machine_install(chrome_exe: Path, version: str = "153.0.8010.53") -> FakeRegistry:
    registry = FakeRegistry()
    registry.set("HKLM", APP_PATHS_KEY, "", str(chrome_exe))
    registry.set(*UNINSTALL_KEYS[0], "DisplayVersion", version)
    return registry


def test_detect_not_installed(registry):
    assert not detect_chrome(registry).installed


def test_detect_machine_install(chrome_exe):
    chrome = detect_chrome(machine_install(chrome_exe))
    assert chrome.installed
    assert chrome.scope == SCOPE_MACHINE
    assert chrome.version == "153.0.8010.53"
    assert chrome.path == str(chrome_exe)


def test_detect_user_install_with_beacon_version(chrome_exe):
    registry = FakeRegistry()
    registry.set("HKCU", APP_PATHS_KEY, "", f'"{chrome_exe}"')
    registry.set("HKCU", BEACON_KEY, "version", "150.0.1")
    chrome = detect_chrome(registry)
    assert chrome.scope == SCOPE_USER
    assert chrome.version == "150.0.1"


def test_detect_from_install_location(chrome_exe):
    registry = FakeRegistry()
    registry.set(*UNINSTALL_KEYS[0], "DisplayVersion", "153.0")
    registry.set(*UNINSTALL_KEYS[0], "InstallLocation", str(chrome_exe.parent))
    chrome = detect_chrome(registry)
    assert chrome.installed and chrome.scope == SCOPE_MACHINE


def test_stale_registry_entry_is_not_installed(tmp_path):
    registry = FakeRegistry()
    registry.set("HKLM", APP_PATHS_KEY, "", str(tmp_path / "Google/Chrome/Application/chrome.exe"))
    assert not detect_chrome(registry).installed


def test_non_stable_channel_is_ignored(tmp_path):
    beta = tmp_path / "Google" / "Chrome Beta" / "Application" / "chrome.exe"
    beta.parent.mkdir(parents=True)
    beta.write_bytes(b"")
    registry = FakeRegistry()
    registry.set("HKLM", APP_PATHS_KEY, "", str(beta))
    assert not detect_chrome(registry).installed


def test_not_installed_is_skipped_without_running_winget(registry):
    runner = FakeRunner({})
    result = process_chrome(registry, WINGET, apply=True, is_admin=True, runner=runner)
    assert result.status is Status.SKIPPED
    assert runner.calls == []


def test_missing_winget_is_warning(chrome_exe):
    result = process_chrome(machine_install(chrome_exe), None, apply=False, is_admin=False)
    assert result.status is Status.WARNING
    assert "Winget indisponível" in result.summary


def test_check_falls_back_to_exe_package_and_reports_update(chrome_exe):
    runner = FakeRunner(
        {
            ("list", "Google.Chrome"): NOT_FOUND,
            ("list", "Google.Chrome.EXE"): listing(
                "Google.Chrome.EXE", "153.0.8010.53", "154.0.8037.58"
            ),
        }
    )
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=False, is_admin=False, runner=runner
    )
    assert result.status is Status.WARNING
    assert result.tag == "UPDATE"
    assert result.details["package_id"] == "Google.Chrome.EXE"
    assert "153.0.8010.53 -> 154.0.8037.58" in result.summary
    assert runner.subcommands() == ["list", "list"]


def test_up_to_date(chrome_exe):
    runner = FakeRunner({("list", "Google.Chrome"): listing("Google.Chrome", "154.0.8037.58")})
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=True, is_admin=True, runner=runner
    )
    assert result.status is Status.SUCCESS
    assert result.details["updated"] is False
    assert runner.subcommands() == ["list"]


def test_not_associated_with_stable_package(chrome_exe):
    runner = FakeRunner(
        {("list", "Google.Chrome"): NOT_FOUND, ("list", "Google.Chrome.EXE"): NOT_FOUND}
    )
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=False, is_admin=False, runner=runner
    )
    assert result.status is Status.WARNING
    assert "não associado" in result.summary


def test_winget_query_failure_is_failed(chrome_exe):
    runner = FakeRunner({("list", "Google.Chrome"): command_result(None, timed_out=True)})
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=False, is_admin=False, runner=runner
    )
    assert result.status is Status.FAILED
    assert result.errors


def update_runner(upgrade_result) -> FakeRunner:
    return FakeRunner(
        {
            ("list", "Google.Chrome"): listing("Google.Chrome", "153.0", "154.0"),
            ("upgrade", "Google.Chrome"): upgrade_result,
        }
    )


def test_machine_install_without_admin_is_not_updated(chrome_exe):
    runner = update_runner(command_result(0))
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=True, is_admin=False, runner=runner
    )
    assert result.status is Status.WARNING
    assert "requer privilégios administrativos" in result.summary
    assert "upgrade" not in runner.subcommands()


def test_dry_run_does_not_update(chrome_exe):
    runner = update_runner(command_result(0))
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=True, is_admin=True, dry_run=True, runner=runner
    )
    assert result.status is Status.WARNING and result.tag == "UPDATE"
    assert "upgrade" not in runner.subcommands()


def test_successful_update(chrome_exe):
    runner = update_runner(command_result(0))
    result = process_chrome(
        machine_install(chrome_exe), WINGET, apply=True, is_admin=True, runner=runner
    )
    assert result.status is Status.SUCCESS
    assert result.details["updated"] is True
    assert result.tag == "OK"
    assert "Atualizado de 153.0 para 154.0" in result.summary
    assert "reiniciado" in result.summary  # versão no Registro ainda é a antiga


def test_successful_update_already_applied(chrome_exe):
    registry = machine_install(chrome_exe, version="153.0")

    def respond(args):
        if args[1] == "list":
            return listing("Google.Chrome", "153.0", "154.0")
        registry.set(*UNINSTALL_KEYS[0], "DisplayVersion", "154.0")  # instalador concluiu
        return command_result(0)

    result = process_chrome(registry, WINGET, apply=True, is_admin=True, runner=FakeRunner(respond))
    assert result.status is Status.SUCCESS
    assert result.details["version_after"] == "154.0"
    assert "reiniciado" not in result.summary


def test_user_install_is_updated_without_admin(chrome_exe):
    registry = FakeRegistry()
    registry.set("HKCU", APP_PATHS_KEY, "", str(chrome_exe))
    registry.set(*UNINSTALL_KEYS[2], "DisplayVersion", "153.0")
    runner = update_runner(command_result(0))
    result = process_chrome(registry, WINGET, apply=True, is_admin=False, runner=runner)
    assert result.status is Status.SUCCESS
    assert runner.subcommands() == ["list", "upgrade"]


@pytest.mark.parametrize(
    ("upgrade", "status", "text"),
    [
        (command_result(0x8A150101), Status.FAILED, "em execução"),
        (command_result(None, timed_out=True), Status.FAILED, "tempo limite"),
        (command_result(UPDATE_NOT_APPLICABLE), Status.SUCCESS, "já atualizado"),
        (command_result(REBOOT_REQUIRED_TO_FINISH), Status.SUCCESS, "reinicialização"),
    ],
)
def test_update_outcomes(chrome_exe, upgrade, status, text):
    result = process_chrome(
        machine_install(chrome_exe),
        WINGET,
        apply=True,
        is_admin=True,
        runner=update_runner(upgrade),
    )
    assert result.status is status
    assert text in result.summary
    if upgrade.exit_code == REBOOT_REQUIRED_TO_FINISH:
        assert result.details["reboot_required"] is True
