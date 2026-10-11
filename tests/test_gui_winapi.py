"""Testes das integrações com o Windows usadas pela interface (sem efeitos reais)."""

import sys
from pathlib import Path

from maintenance.gui import winapi


def test_relaunch_command_for_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Windows-Maintenance-Agent-v1.0.0.exe")
    assert winapi.relaunch_command("maintenance") == (
        r"C:\Apps\Windows-Maintenance-Agent-v1.0.0.exe",
        "--page maintenance",
    )
    assert winapi.relaunch_command() == (r"C:\Apps\Windows-Maintenance-Agent-v1.0.0.exe", "")


def test_relaunch_command_from_source_prefers_pythonw(monkeypatch, tmp_path):
    python = tmp_path / "Python Folder" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    executable, parameters = winapi.relaunch_command("check")
    assert executable == str(python)  # pythonw.exe ausente
    assert parameters == "-m maintenance.gui --page check"
    (python.parent / "pythonw.exe").write_bytes(b"")
    assert winapi.relaunch_command("check")[0] == str(python.parent / "pythonw.exe")


class FakeFunction:
    """Imita uma função do ctypes (aceita ``restype``)."""

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class FakeShell32:
    def __init__(self, result):
        self.ShellExecuteW = FakeFunction(result)  # noqa: N815 - nome da API do Windows


def test_relaunch_as_admin_uses_the_uac_prompt(monkeypatch):
    shell = FakeShell32(42)
    monkeypatch.setattr(winapi.ctypes, "WinDLL", lambda name: shell)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\wma.exe")
    assert winapi.relaunch_as_admin("maintenance") is True
    (_hwnd, verb, executable, parameters, _directory, show) = shell.ShellExecuteW.calls[0]
    assert (verb, executable, parameters, show) == (
        "runas",
        r"C:\Apps\wma.exe",
        "--page maintenance",
        1,
    )


def test_relaunch_as_admin_reports_refusal(monkeypatch):
    # ShellExecuteW retorna <= 32 quando o usuário recusa o prompt do UAC.
    monkeypatch.setattr(winapi.ctypes, "WinDLL", lambda name: FakeShell32(5))
    assert winapi.relaunch_as_admin() is False
    monkeypatch.setattr(winapi.ctypes, "WinDLL", lambda name: FakeShell32(None))
    assert winapi.relaunch_as_admin() is False

    def unavailable(name):
        raise OSError("sem shell32")

    monkeypatch.setattr(winapi.ctypes, "WinDLL", unavailable)
    assert winapi.relaunch_as_admin() is False


def test_open_path_handles_missing_files(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(winapi.os, "startfile", opened.append, raising=False)
    assert winapi.open_path(tmp_path) is True
    assert opened == [tmp_path]

    def fail(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(winapi.os, "startfile", fail, raising=False)
    assert winapi.open_path(tmp_path / "nao-existe.txt") is False


def test_assets_are_packaged_with_the_module():
    assert winapi.ICON_FILE.is_file() and winapi.ICON_FILE.suffix == ".ico"
    assert winapi.LOGO_FILE.is_file()
    header = Path(winapi.ICON_FILE).read_bytes()[:4]
    assert header == b"\x00\x00\x01\x00"  # cabeçalho de arquivo .ico


def test_wake_event_loop_reports_missing_windows():
    assert winapi.wake_event_loop(0x7FFFFFF0) is False  # handle inexistente
