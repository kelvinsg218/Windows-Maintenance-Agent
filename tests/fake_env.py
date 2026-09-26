"""Ambiente falso completo para testar os fluxos ``check``/``run`` e a CLI.

Nada aqui toca o sistema real: Registro, winget, Windows Update, perfis de usuário e
pasta do Windows são simulados dentro de um diretório temporário do pytest.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from maintenance.chrome import APP_PATHS_KEY, UNINSTALL_KEYS
from maintenance.cleanup import PROFILE_LIST_KEY
from maintenance.config import AppConfig, AppPaths, resolve_paths
from maintenance.privileges import ELEVATION_DEFAULT, ELEVATION_FULL, PrivilegeInfo
from maintenance.process import CommandResult
from maintenance.runner import Services
from maintenance.system_info import DiskUsage, SystemInfo, WindowsVersion
from maintenance.winget import NO_APPLICATIONS_FOUND
from tests.conftest import FakeRegistry
from tests.helpers import FakeRunner, command_result, winget_table
from tests.wua_fakes import FakeClient, FakeUpdate

GB = 1024**3
WINGET = Path(r"C:\fake\WindowsApps\winget.exe")


def age_file(path: Path, days: float) -> None:
    """Envelhece modificação E criação (a limpeza considera a mais recente das duas)."""
    win32file = pytest.importorskip("win32file")
    win32con = pytest.importorskip("win32con")
    pywintypes = pytest.importorskip("pywintypes")
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))
    handle = win32file.CreateFile(
        str(path),
        win32con.GENERIC_WRITE,
        0,
        None,
        win32con.OPEN_EXISTING,
        win32con.FILE_ATTRIBUTE_NORMAL,
        None,
    )
    try:
        stamp = pywintypes.Time(moment)
        win32file.SetFileTime(handle, stamp, stamp, stamp)
    finally:
        handle.Close()


class SequentialClock:
    """Relógio determinístico: cada chamada avança alguns segundos."""

    def __init__(self, start: datetime | None = None, step_seconds: int = 7) -> None:
        self.current = start or datetime(2026, 9, 26, 8, 0, 1).astimezone()
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value


@dataclass
class FakeEnvironment:
    services: Services
    runner: FakeRunner
    registry: FakeRegistry
    config: AppConfig
    paths: AppPaths
    user_temp: Path
    old_file: Path
    recent_file: Path
    wua_clients: list[FakeClient] = field(default_factory=list)

    @property
    def wua(self) -> FakeClient:
        return self.wua_clients[-1]


def build_environment(
    tmp_path: Path,
    *,
    admin: bool = False,
    winget: bool = True,
    chrome_available: str = "154.0.8037.58",
    chrome_upgrade: CommandResult | None = None,
    wua_updates: list[FakeUpdate] | None = None,
    wua_options: dict | None = None,
    config: dict | None = None,
) -> FakeEnvironment:
    registry = FakeRegistry()

    chrome_exe = tmp_path / "Program Files" / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome_exe.parent.mkdir(parents=True)
    chrome_exe.write_bytes(b"")
    registry.set("HKLM", APP_PATHS_KEY, "", str(chrome_exe))
    registry.set(*UNINSTALL_KEYS[0], "DisplayVersion", "153.0.8010.53")

    windows = tmp_path / "Windows"
    (windows / "Temp").mkdir(parents=True)
    users = tmp_path / "Users"
    profile = users / "tecnico"
    user_temp = profile / "AppData" / "Local" / "Temp"
    user_temp.mkdir(parents=True)
    registry.set("HKLM", PROFILE_LIST_KEY, "ProfilesDirectory", str(users))
    registry.set(
        "HKLM", rf"{PROFILE_LIST_KEY}\S-1-5-21-1-2-3-1001", "ProfileImagePath", str(profile)
    )
    old_file = user_temp / "old-installer" / "setup.tmp"
    old_file.parent.mkdir()
    old_file.write_bytes(b"x" * 4096)
    age_file(old_file, 30)
    recent_file = user_temp / "recent.tmp"
    recent_file.write_bytes(b"y" * 10)

    listing = winget_table(
        ["Nome", "ID", "Versão", "Disponível", "Origem"],
        [14, 18, 14, 14, 6],
        [["Google Chrome", "Google.Chrome.EXE", "153.0.8010.53", chrome_available, "winget"]],
    )

    def respond(args: list[str]) -> CommandResult:
        if args[1] == "--version":
            return command_result(0, "v1.29.380\r\n")
        if args[1] == "list" and args[3] == "Google.Chrome":
            return command_result(NO_APPLICATIONS_FOUND, "Nenhum pacote instalado.\r\n")
        if args[1] == "list":
            return command_result(0, listing)
        if args[1] == "upgrade":
            return chrome_upgrade or command_result(0)
        raise AssertionError(f"comando inesperado: {args}")

    runner = FakeRunner(respond)
    updates = wua_updates if wua_updates is not None else [FakeUpdate("Cumulative (KB5065789)")]
    clients: list[FakeClient] = []

    def wua_factory() -> FakeClient:
        client = FakeClient(list(updates), **(wua_options or {}))
        clients.append(client)
        return client

    services = Services(
        registry=registry,
        get_privileges=lambda: PrivilegeInfo(admin, ELEVATION_FULL if admin else ELEVATION_DEFAULT),
        collect_system_info=lambda _registry: SystemInfo(
            "TI-04",
            "tecnico",
            WindowsVersion("Windows 11 Pro", "24H2", "26100.4061"),
            "x64",
            DiskUsage("C:", 256 * GB, 100 * GB),
            3 * 3600.0,
        ),
        find_winget=lambda: WINGET if winget else None,
        run_command=runner,
        wua_client_factory=wua_factory,
        disk_usage=lambda drive: DiskUsage(drive, 256 * GB, 101 * GB),
        system_drive=lambda: "C:",
        now=SequentialClock(),
        cleanup_env={"USERPROFILE": str(profile)},
        windows_dir=windows,
    )
    app_config = AppConfig(data_dir=str(tmp_path / "agent"), **(config or {}))
    return FakeEnvironment(
        services=services,
        runner=runner,
        registry=registry,
        config=app_config,
        paths=resolve_paths(app_config),
        user_temp=user_temp,
        old_file=old_file,
        recent_file=recent_file,
        wua_clients=clients,
    )
