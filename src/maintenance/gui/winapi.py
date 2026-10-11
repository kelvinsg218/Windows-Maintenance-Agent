"""Integrações com o Windows usadas pela interface gráfica.

A elevação de privilégios usa somente o caminho oficial: o Windows exibe o prompt do
UAC e o usuário decide. Nada aqui contorna o UAC ou altera configurações do sistema.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

APP_USER_MODEL_ID = "WindowsMaintenanceAgent.Desktop"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_FILE = ASSETS_DIR / "app.ico"
LOGO_FILE = ASSETS_DIR / "logo.png"

_SW_SHOWNORMAL = 1
_SHELL_EXECUTE_OK = 32  # ShellExecuteW: valores > 32 indicam sucesso
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Windows 10 1809 a 1909


def enable_dpi_awareness() -> None:
    """Texto nítido em telas com escala (deve ser chamado antes de criar a janela)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def set_app_user_model_id() -> None:
    """Agrupa a janela na barra de tarefas com o ícone do aplicativo."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def work_area() -> tuple[int, int, int, int] | None:
    """Área útil da tela principal, sem a barra de tarefas: (esquerda, topo, direita, base)."""
    try:
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return None


def use_dark_title_bar(window_handle: int) -> None:
    """Barra de título escura (Windows 10 1809+ e Windows 11); ignorado se indisponível."""
    try:
        value = ctypes.c_int(1)
        dwmapi = ctypes.windll.dwmapi
        for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            result = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(window_handle),
                attribute,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                return
    except (AttributeError, OSError):
        pass


def wake_event_loop(window_handle: int) -> bool:
    """Posta WM_NULL (mensagem vazia) para acordar o laço de eventos da janela.

    Retorna ``False`` se a janela não existe mais.
    """
    try:
        return bool(ctypes.windll.user32.PostMessageW(ctypes.c_void_p(window_handle), 0, 0, 0))
    except (AttributeError, OSError):
        return False


def show_error(title: str, message: str) -> None:
    """Caixa de mensagem nativa, usada quando a própria janela não pôde ser criada."""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except (AttributeError, OSError):
        pass


def open_path(path: Path) -> bool:
    """Abre um arquivo ou pasta com o programa padrão do Windows (Bloco de Notas, Explorer)."""
    try:
        os.startfile(path)  # noqa: S606 - caminho gerado pelo próprio agente (log/relatório)
    except OSError:
        return False
    return True


def relaunch_command(page: str | None = None) -> tuple[str, str]:
    """Programa e parâmetros para abrir uma nova instância da interface."""
    arguments: list[str] = []
    if getattr(sys, "frozen", False):
        executable = sys.executable
    else:
        # Código-fonte: pythonw.exe evita abrir uma janela de console.
        python = Path(sys.executable)
        windowed = python.with_name("pythonw.exe")
        executable = str(windowed if windowed.is_file() else python)
        arguments += ["-m", "maintenance.gui"]
    if page:
        arguments += ["--page", page]
    return executable, subprocess.list2cmdline(arguments)


def relaunch_as_admin(page: str | None = None) -> bool:
    """Pede ao Windows para abrir o aplicativo como administrador (prompt do UAC).

    Retorna ``False`` se o usuário recusar o prompt ou se a elevação não for possível.
    """
    executable, parameters = relaunch_command(page)
    try:
        shell32 = ctypes.WinDLL("shell32")
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        result = shell32.ShellExecuteW(
            None, "runas", executable, parameters or None, None, _SW_SHOWNORMAL
        )
    except (AttributeError, OSError):
        return False
    return (result or 0) > _SHELL_EXECUTE_OK
