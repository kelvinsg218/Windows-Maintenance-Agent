"""Execução segura de processos externos e de operações com tempo limite."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_KILL_GRACE_SECONDS = 10
_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class CommandResult:
    """Resultado completo de um processo externo."""

    args: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    def describe_exit(self) -> str:
        if self.error:
            return f"falha ao iniciar o processo: {self.error}"
        if self.timed_out:
            return "tempo limite excedido"
        return f"código de saída {format_exit_code(self.exit_code)}"


CommandRunner = Callable[..., CommandResult]


class OperationTimeoutError(TimeoutError):
    """Uma operação excedeu o tempo limite configurado."""


def format_exit_code(code: int | None) -> str:
    """Exit codes do tipo HRESULT são exibidos em hexadecimal (ex.: 0x8A150014)."""
    if code is None:
        return "-"
    if code >= 0x80000000:
        return f"0x{code:08X}"
    return str(code)


def run_command(args: Sequence[str], *, timeout: float, cwd: Path | None = None) -> CommandResult:
    """Executa um programa a partir de uma lista de argumentos, nunca via shell.

    - sem ``shell=True``: nada é interpretado por cmd.exe/PowerShell;
    - stdin fechado: o processo não consegue aguardar interação;
    - stdout/stderr capturados (UTF-8) e duração medida;
    - exit code normalizado para 32 bits sem sinal (HRESULTs como 0x8A150014);
    - ao exceder ``timeout`` o processo é encerrado.
    """
    argv = list(args)
    if not argv or not all(isinstance(arg, str) and arg for arg in argv):
        raise ValueError("args deve ser uma lista não vazia de strings não vazias")
    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - argumentos definidos pelo agente, sem shell
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            shell=False,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return CommandResult(tuple(argv), None, "", "", time.monotonic() - started, error=str(exc))

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            # Um processo filho pode manter os pipes abertos; não esperamos indefinidamente.
            stdout, stderr = b"", b""
    except BaseException:
        process.kill()
        raise
    exit_code = None if timed_out else process.returncode & 0xFFFFFFFF
    return CommandResult(
        tuple(argv),
        exit_code,
        _decode(stdout),
        _decode(stderr),
        time.monotonic() - started,
        timed_out=timed_out,
    )


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def call_with_timeout(func: Callable[[], T], timeout: float, *, name: str = "operação") -> T:
    """Executa ``func`` em uma thread auxiliar e aguarda no máximo ``timeout`` segundos.

    Usado para operações que não oferecem timeout próprio (COM do Windows Update,
    cópia para compartilhamento de rede). Em caso de timeout a thread é abandonada
    (daemon) e :class:`OperationTimeoutError` é lançada, permitindo que os demais
    módulos da manutenção continuem.
    """
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = func()
        except Exception as exc:  # repassada para a thread principal
            outcome["error"] = exc

    worker = threading.Thread(target=target, name=f"wma-{name}", daemon=True)
    worker.start()
    wait_for(worker.is_alive, timeout, name=name, join=worker.join)
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def wait_for(
    still_running: Callable[[], bool],
    timeout: float,
    *,
    name: str,
    join: Callable[[float], Any],
) -> None:
    """Aguarda em pequenos intervalos (permite Ctrl+C) até o fim ou o timeout."""
    deadline = time.monotonic() + timeout
    while still_running():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OperationTimeoutError(f"{name}: tempo limite de {timeout:.0f}s excedido")
        join(min(remaining, _POLL_SECONDS))
