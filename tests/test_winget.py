from pathlib import Path

import pytest

from maintenance.results import Status
from maintenance.winget import (
    NO_APPLICATIONS_FOUND,
    WingetError,
    WingetInfo,
    clean_output,
    describe_exit_code,
    find_package,
    find_winget,
    get_winget_info,
    list_package,
    package_version_key,
    parse_list_output,
    upgrade_package,
    winget_result,
)
from tests.helpers import FakeRunner, command_result, winget_table

# Saídas reais capturadas no Windows 11 pt-BR (winget v1.29.380).
REAL_PT_BR_WITH_UPDATE = (
    "Nome          ID                Versão        Disponível    Origem\r\n"
    "------------------------------------------------------------------\r\n"
    "Google Chrome Google.Chrome.EXE 153.0.8010.53 154.0.8037.58 winget\r\n"
)
# Com --source winget (como o agente executa) a coluna "Origem" é omitida.
REAL_PT_BR_SOURCE_FILTERED_WITH_UPDATE = (
    "Nome          ID                Versão        Disponível\r\n"
    "-----------------------------------------------------------\r\n"
    "Google Chrome Google.Chrome.EXE 153.0.8010.53 154.0.8037.58\r\n"
)
PT_BR_SOURCE_FILTERED_UP_TO_DATE = winget_table(
    ["Nome", "ID", "Versão"],
    [14, 18, 13],
    [["Google Chrome", "Google.Chrome.EXE", "154.0.8037.58"]],
)
REAL_PT_BR_UPGRADE_AVAILABLE_FOOTER = REAL_PT_BR_WITH_UPDATE + "1 atualizações disponíveis.\r\n"
REAL_PT_BR_NOT_FOUND = (
    "Nenhum pacote instalado foi encontrado que corresponda aos critérios de entrada.\r\n"
)
EN_US_UP_TO_DATE = winget_table(
    ["Name", "Id", "Version", "Source"],
    [14, 18, 14, 6],
    [["Google Chrome", "Google.Chrome", "154.0.8037.58", "winget"]],
)
WINGET = Path(r"C:\Users\ti\AppData\Local\Microsoft\WindowsApps\winget.exe")


def test_parse_real_pt_br_output_with_available_column():
    package = find_package(REAL_PT_BR_WITH_UPDATE, "Google.Chrome.EXE")
    assert package is not None
    assert package.name == "Google Chrome"
    assert package.version == "153.0.8010.53"
    assert package.available == "154.0.8037.58"
    assert package.source == "winget"


def test_parse_real_source_filtered_output_with_update():
    """Regressão: com --source, a 4ª coluna é "Disponível" (não "Origem")."""
    package = find_package(
        REAL_PT_BR_SOURCE_FILTERED_WITH_UPDATE, "Google.Chrome.EXE", source_filtered=True
    )
    assert package.version == "153.0.8010.53"
    assert package.available == "154.0.8037.58"
    assert package.source is None


def test_parse_source_filtered_output_up_to_date():
    package = find_package(
        PT_BR_SOURCE_FILTERED_UP_TO_DATE, "Google.Chrome.EXE", source_filtered=True
    )
    assert package.version == "154.0.8037.58"
    assert package.available is None


def test_footer_lines_are_ignored():
    packages = parse_list_output(REAL_PT_BR_UPGRADE_AVAILABLE_FOOTER)
    assert find_package(REAL_PT_BR_UPGRADE_AVAILABLE_FOOTER, "Google.Chrome.EXE").available
    assert all(package.package_id != "Google.Chrome" for package in packages)


def test_parse_en_us_output_without_available_column():
    package = find_package(EN_US_UP_TO_DATE, "google.chrome")
    assert package is not None
    assert package.version == "154.0.8037.58"
    assert package.available is None
    assert package.source == "winget"


def test_parse_rows_with_and_without_update_and_spaces_in_values():
    output = winget_table(
        ["Name", "Id", "Version", "Available", "Source"],
        [22, 20, 12, 12, 6],
        [
            ["Google Chrome", "Google.Chrome", "153.0", "154.0", "winget"],
            ["Git", "Git.Git", "2.49.0", "", "winget"],
            ["Some App", "Vendor.App", "< 1.2.3", "", "winget"],
        ],
    )
    packages = {package.package_id: package for package in parse_list_output(output)}
    assert packages["Google.Chrome"].available == "154.0"
    assert packages["Git.Git"].available is None
    assert packages["Vendor.App"].version == "< 1.2.3"


def test_progress_indicators_and_ansi_sequences_are_removed():
    noisy = "   -\r   \\\r   |\r\x1b[0m" + REAL_PT_BR_WITH_UPDATE
    assert clean_output(noisy)[0].startswith("Nome")
    assert find_package(noisy, "Google.Chrome.EXE").available == "154.0.8037.58"


def test_output_without_table_yields_nothing():
    assert parse_list_output(REAL_PT_BR_NOT_FOUND) == []
    assert find_package("", "Google.Chrome") is None


def test_unexpected_table_layout_raises():
    output = winget_table(["A", "B"], [10, 10], [["1", "2"]])
    with pytest.raises(WingetError, match="não reconhecido"):
        parse_list_output(output)


def test_list_package_found_not_found_and_failures():
    runner = FakeRunner(
        {("list", "Google.Chrome.EXE"): command_result(0, REAL_PT_BR_SOURCE_FILTERED_WITH_UPDATE)}
    )
    package = list_package(WINGET, "Google.Chrome.EXE", timeout=60, runner=runner)
    assert package.available == "154.0.8037.58"

    runner = FakeRunner(
        {("list", "Google.Chrome.EXE"): command_result(0, PT_BR_SOURCE_FILTERED_UP_TO_DATE)}
    )
    assert list_package(WINGET, "Google.Chrome.EXE", timeout=60, runner=runner).available is None

    runner = FakeRunner(
        {("list", "Google.Chrome"): command_result(NO_APPLICATIONS_FOUND, REAL_PT_BR_NOT_FOUND)}
    )
    assert list_package(WINGET, "Google.Chrome", timeout=60, runner=runner) is None

    for failure in (
        command_result(None, timed_out=True),
        command_result(None, error="arquivo não encontrado"),
        command_result(0x8A15000F),
    ):
        runner = FakeRunner({("list", "Google.Chrome"): failure})
        with pytest.raises(WingetError):
            list_package(WINGET, "Google.Chrome", timeout=60, runner=runner)


def test_commands_use_exact_id_fixed_source_and_no_interaction():
    runner = FakeRunner(
        {
            ("list", "Google.Chrome"): command_result(0, PT_BR_SOURCE_FILTERED_UP_TO_DATE),
            ("upgrade", "Google.Chrome"): command_result(0),
        }
    )
    list_package(WINGET, "Google.Chrome", timeout=60, runner=runner)
    upgrade_package(WINGET, "Google.Chrome", timeout=60, runner=runner)
    list_args, upgrade_args = runner.calls
    for args in (list_args, upgrade_args):
        assert args[0] == str(WINGET)
        assert args[2:5] == ["--id", "Google.Chrome", "--exact"]
        assert "--disable-interactivity" in args
        assert args[args.index("--source") + 1] == "winget"
    assert {"--silent", "--accept-package-agreements"} <= set(upgrade_args)


@pytest.mark.parametrize("bad_id", ["Google.Chrome & calc", "", "-x", "a b", "Google.Chrome;rm"])
def test_unsafe_package_ids_are_rejected_before_running(bad_id):
    runner = FakeRunner({})
    with pytest.raises(ValueError):
        upgrade_package(WINGET, bad_id, timeout=60, runner=runner)
    assert runner.calls == []


def test_describe_exit_codes():
    assert describe_exit_code(0x8A150101).startswith("0x8A150101 (o aplicativo está em execução")
    assert describe_exit_code(0x8A15FFFF) == "0x8A15FFFF"
    assert describe_exit_code(1) == "1"


def test_find_winget_prefers_path_then_user_alias(monkeypatch, tmp_path):
    monkeypatch.setattr("maintenance.winget.shutil.which", lambda name: None)
    alias = tmp_path / "Microsoft" / "WindowsApps" / "winget.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    env = {"LOCALAPPDATA": str(tmp_path), "ProgramFiles": str(tmp_path / "pf")}
    assert find_winget(env) == alias


def test_find_winget_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("maintenance.winget.shutil.which", lambda name: None)
    env = {"LOCALAPPDATA": str(tmp_path / "none"), "ProgramFiles": str(tmp_path / "pf")}
    assert find_winget(env) is None


def test_find_winget_in_package_folder_picks_newest_version(monkeypatch, tmp_path):
    """Caso da conta SYSTEM: sem alias, usa a pasta do pacote App Installer."""
    monkeypatch.setattr("maintenance.winget.shutil.which", lambda name: None)
    apps = tmp_path / "WindowsApps"
    for version in ("1.2.3.0", "1.10.0.0"):
        folder = apps / f"Microsoft.DesktopAppInstaller_{version}_x64__8wekyb3d8bbwe"
        folder.mkdir(parents=True)
        (folder / "winget.exe").write_bytes(b"")
    found = find_winget({"ProgramFiles": str(tmp_path)})
    assert found is not None and "_1.10.0.0_" in str(found)
    assert package_version_key(found.parent) == (1, 10, 0, 0)

    runner = FakeRunner(lambda args: command_result(0, "v1.10.0\r\n"))
    get_winget_info(found, runner=runner)
    assert runner.calls[0][1] == "--version"


def test_winget_info_and_result():
    runner = FakeRunner(lambda args: command_result(0, "v1.29.380\r\n"))
    info = get_winget_info(WINGET, runner=runner)
    assert info == WingetInfo(WINGET, "1.29.380")
    assert winget_result(info).status is Status.SUCCESS
    assert "v1.29.380" in winget_result(info).summary

    failing = FakeRunner(lambda args: command_result(None, error="boom"))
    assert get_winget_info(WINGET, runner=failing).version is None
    assert get_winget_info(None) is None
    missing = winget_result(None)
    assert missing.status is Status.WARNING
    assert missing.details["available"] is False
