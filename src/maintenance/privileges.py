"""Verificação de privilégios administrativos.

O agente apenas *detecta* o nível de privilégio: nunca tenta se elevar nem contornar
o UAC. Operações que exigem administrador são ignoradas (e registradas) quando o
processo não está elevado.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from maintenance.results import ModuleResult, Status

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_TYPE_CLASS = 18  # TOKEN_INFORMATION_CLASS.TokenElevationType

ELEVATION_DEFAULT = "default"  # sem token dividido: usuário padrão, UAC desligado ou SYSTEM
ELEVATION_FULL = "full"  # processo elevado
ELEVATION_LIMITED = "limited"  # administrador executando sem elevação
ELEVATION_UNKNOWN = "unknown"

_ELEVATION_TYPES = {1: ELEVATION_DEFAULT, 2: ELEVATION_FULL, 3: ELEVATION_LIMITED}


@dataclass(frozen=True)
class PrivilegeInfo:
    is_admin: bool
    elevation_type: str = ELEVATION_UNKNOWN

    @property
    def can_elevate(self) -> bool:
        """O usuário é administrador, mas este processo não foi iniciado elevado."""
        return not self.is_admin and self.elevation_type == ELEVATION_LIMITED

    def label(self) -> str:
        if self.is_admin:
            return "Sim"
        if self.can_elevate:
            return 'Não (usuário administrador sem elevação: use "Executar como administrador")'
        return "Não"


def is_admin() -> bool:
    """True se o processo atual possui privilégios administrativos efetivos (elevado)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def elevation_type() -> str:
    if sys.platform != "win32":
        return ELEVATION_UNKNOWN
    try:
        value = _query_token_elevation_type()
    except (AttributeError, OSError):
        return ELEVATION_UNKNOWN
    return (
        _ELEVATION_TYPES.get(value, ELEVATION_UNKNOWN) if value is not None else ELEVATION_UNKNOWN
    )


def get_privilege_info() -> PrivilegeInfo:
    return PrivilegeInfo(is_admin=is_admin(), elevation_type=elevation_type())


def privilege_result(info: PrivilegeInfo) -> ModuleResult:
    details = {"is_admin": info.is_admin, "elevation_type": info.elevation_type}
    if info.is_admin:
        return ModuleResult(
            "privileges", Status.SUCCESS, "Executando com privilégios administrativos", details
        )
    summary = (
        "Executando sem privilégios administrativos: instalação de atualizações do Windows, "
        "atualização de programas instalados por máquina e limpeza de áreas do sistema "
        "serão ignoradas"
    )
    if info.can_elevate:
        summary += ' (abra o terminal com "Executar como administrador")'
    return ModuleResult("privileges", Status.WARNING, summary, details)


def _query_token_elevation_type() -> int | None:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return None
    try:
        value = wintypes.DWORD()
        returned = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(
            token,
            TOKEN_ELEVATION_TYPE_CLASS,
            ctypes.byref(value),
            ctypes.sizeof(value),
            ctypes.byref(returned),
        )
        return value.value if ok else None
    finally:
        kernel32.CloseHandle(token)
