"""Construtores de dados falsos reutilizados pelos testes."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from maintenance.process import CommandResult


def command_result(
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    *,
    timed_out: bool = False,
    error: str | None = None,
    duration: float = 1.0,
) -> CommandResult:
    return CommandResult(("fake",), exit_code, stdout, stderr, duration, timed_out, error)


def winget_table(headers: Sequence[str], widths: Sequence[int], rows: Sequence[Sequence[str]]):
    """Monta uma tabela no formato do ``winget list`` (colunas alinhadas por espaços)."""

    def line(cells: Sequence[str]) -> str:
        return "".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    lines = [line(headers).rstrip(), "-" * sum(widths)]
    lines.extend(line(row).rstrip() for row in rows)
    return "\r\n".join(lines) + "\r\n"


class FakeRunner:
    """Substitui ``run_command``: responde conforme o subcomando e o ID do pacote."""

    def __init__(self, responses: dict[tuple[str, ...], CommandResult] | Callable) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, *, timeout, cwd=None) -> CommandResult:
        args = list(args)
        self.calls.append(args)
        if callable(self.responses):
            return self.responses(args)
        key = (args[1], args[3]) if len(args) > 3 else (args[1],)
        if key not in self.responses:
            raise AssertionError(f"comando inesperado: {args}")
        return self.responses[key]

    def subcommands(self) -> list[str]:
        return [call[1] for call in self.calls]
