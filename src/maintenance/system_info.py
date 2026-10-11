"""Coleta de informações da máquina úteis à manutenção.

Coleta apenas o necessário para identificar a estação e avaliar seu estado
(nome, usuário da sessão, versão do Windows, arquitetura, disco e uptime; o painel
da interface gráfica também exibe processador e memória RAM).
Nenhum dado pessoal adicional é lido.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from maintenance.formatting import format_bytes
from maintenance.registry import HKLM, RegistryReader
from maintenance.results import ModuleResult, Status

NT_CURRENT_VERSION = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
PROCESSOR_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
# O Registro continua informando "Windows 10" no ProductName do Windows 11;
# a distinção oficial é o número do build.
WINDOWS_11_FIRST_BUILD = 22000

_ARCHITECTURES = {
    "AMD64": "x64",
    "X86_64": "x64",
    "X64": "x64",
    "ARM64": "ARM64",
    "AARCH64": "ARM64",
    "X86": "x86",
    "I386": "x86",
    "I686": "x86",
}


@dataclass(frozen=True)
class WindowsVersion:
    product_name: str
    display_version: str
    build: str

    @property
    def short(self) -> str:
        return f"{self.product_name} {self.display_version}".strip()

    @property
    def full(self) -> str:
        return f"{self.short} (build {self.build})" if self.build else self.short


@dataclass(frozen=True)
class DiskUsage:
    drive: str
    total_bytes: int
    free_bytes: int

    @property
    def used_bytes(self) -> int:
        return max(self.total_bytes - self.free_bytes, 0)

    @property
    def used_percent(self) -> float:
        return 100.0 * self.used_bytes / self.total_bytes if self.total_bytes else 0.0

    @property
    def free_percent(self) -> float:
        return 100.0 * self.free_bytes / self.total_bytes if self.total_bytes else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "drive": self.drive,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "used_percent": round(self.used_percent, 1),
        }


@dataclass(frozen=True)
class MemoryStatus:
    total_bytes: int  # memória utilizável pelo Windows
    available_bytes: int
    installed_bytes: int | None = None  # memória instalada (pode ser maior que a utilizável)

    @property
    def used_percent(self) -> float:
        used = max(self.total_bytes - self.available_bytes, 0)
        return 100.0 * used / self.total_bytes if self.total_bytes else 0.0


@dataclass(frozen=True)
class SystemInfo:
    hostname: str
    username: str
    windows: WindowsVersion
    architecture: str
    disk: DiskUsage | None
    uptime_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "username": self.username,
            "windows": asdict(self.windows),
            "architecture": self.architecture,
            "disk": self.disk.to_dict() if self.disk else None,
            "uptime_seconds": round(self.uptime_seconds) if self.uptime_seconds else None,
        }


def get_hostname() -> str:
    name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "DESCONHECIDO"
    return name.strip()


def get_username() -> str:
    """Nome da conta que executa o agente (``SYSTEM`` em tarefas agendadas como sistema)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            size = wintypes.DWORD(257)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
                return buffer.value
        except (AttributeError, OSError):
            pass
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # getpass pode falhar sem variáveis de ambiente de usuário
        return "desconhecido"


def normalize_product_name(product_name: str, build: str) -> str:
    try:
        build_number = int(build.split(".")[0])
    except ValueError:
        return product_name
    if build_number >= WINDOWS_11_FIRST_BUILD and product_name.startswith("Windows 10"):
        return "Windows 11" + product_name[len("Windows 10") :]
    return product_name


def get_windows_version(registry: RegistryReader) -> WindowsVersion:
    def read(name: str) -> str:
        value = registry.get_value(HKLM, NT_CURRENT_VERSION, name)
        return str(value).strip() if value is not None else ""

    product = read("ProductName") or f"{platform.system()} {platform.release()}".strip()
    display = read("DisplayVersion") or read("ReleaseId")
    build = read("CurrentBuildNumber") or read("CurrentBuild")
    if not build:
        build = platform.version().rsplit(".", 1)[-1] if platform.version() else ""
    ubr = registry.get_value(HKLM, NT_CURRENT_VERSION, "UBR")
    full_build = f"{build}.{ubr}" if build and isinstance(ubr, int) else build
    return WindowsVersion(
        normalize_product_name(product or "Desconhecido", build), display, full_build
    )


def get_architecture(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    # Em um processo 32 bits no Windows 64 bits, PROCESSOR_ARCHITEW6432 traz a real.
    raw = env.get("PROCESSOR_ARCHITEW6432") or env.get("PROCESSOR_ARCHITECTURE")
    raw = (raw or platform.machine() or "").upper()
    return _ARCHITECTURES.get(raw, raw or "desconhecida")


def get_system_drive(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    drive = (env.get("SystemDrive") or "C:").strip()
    return drive.upper() if re.fullmatch(r"[A-Za-z]:", drive) else "C:"


def get_disk_usage(drive: str) -> DiskUsage | None:
    try:
        usage = shutil.disk_usage(drive + "\\")
    except OSError:
        return None
    return DiskUsage(drive, usage.total, usage.free)


def get_uptime_seconds() -> float | None:
    """Tempo desde a última inicialização. Com a Inicialização Rápida do Windows ativa,
    "Desligar" não zera este contador (apenas "Reiniciar")."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return kernel32.GetTickCount64() / 1000.0
    except (AttributeError, OSError):
        return None


def get_processor_name(registry: RegistryReader) -> str | None:
    value = registry.get_value(HKLM, PROCESSOR_KEY, "ProcessorNameString")
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())  # o Registro costuma trazer espaços repetidos
    return None


def get_memory_status() -> MemoryStatus | None:
    """Memória física total, disponível e instalada (GlobalMemoryStatusEx)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        installed_kb = ctypes.c_ulonglong(0)
        installed = (
            installed_kb.value * 1024
            if kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(installed_kb))
            else None
        )
        return MemoryStatus(int(status.ullTotalPhys), int(status.ullAvailPhys), installed)
    except (AttributeError, OSError):
        return None


def collect_system_info(registry: RegistryReader) -> SystemInfo:
    return SystemInfo(
        hostname=get_hostname(),
        username=get_username(),
        windows=get_windows_version(registry),
        architecture=get_architecture(),
        disk=get_disk_usage(get_system_drive()),
        uptime_seconds=get_uptime_seconds(),
    )


def evaluate_disk_space(
    disk: DiskUsage | None, *, min_free_percent: int, min_free_gb: int
) -> ModuleResult:
    if disk is None:
        return ModuleResult(
            "disk", Status.WARNING, "Não foi possível obter o uso do disco do sistema"
        )
    details = disk.to_dict()
    free = f"{format_bytes(disk.free_bytes)} livres, {disk.free_percent:.0f}%"
    if disk.free_percent < min_free_percent or disk.free_bytes < min_free_gb * 1024**3:
        return ModuleResult(
            "disk", Status.WARNING, f"Pouco espaço livre em {disk.drive} ({free})", details
        )
    return ModuleResult("disk", Status.SUCCESS, f"Espaço em disco suficiente ({free})", details)
