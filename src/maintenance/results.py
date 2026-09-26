"""Estados padronizados dos módulos e do resultado geral da manutenção."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# Tarefas que alteram o computador. As demais etapas (informações, privilégios,
# winget, disco, reinicialização) são verificações.
TASK_CHROME = "chrome"
TASK_WINDOWS_UPDATE = "windows_update"
TASK_CLEANUP = "cleanup"
TASKS: tuple[str, ...] = (TASK_CHROME, TASK_WINDOWS_UPDATE, TASK_CLEANUP)


class Status(StrEnum):
    """Estado de um módulo."""

    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class OverallResult(StrEnum):
    """Resultado geral de uma execução."""

    SUCCESS = "SUCCESS"
    SUCCESS_WITH_WARNINGS = "SUCCESS_WITH_WARNINGS"
    FAILED = "FAILED"


EXIT_CODES: dict[OverallResult, int] = {
    OverallResult.SUCCESS: 0,
    OverallResult.SUCCESS_WITH_WARNINGS: 1,
    OverallResult.FAILED: 2,
}

OVERALL_LABELS_PT: dict[OverallResult, str] = {
    OverallResult.SUCCESS: "SUCESSO",
    OverallResult.SUCCESS_WITH_WARNINGS: "SUCESSO COM PENDÊNCIAS",
    OverallResult.FAILED: "FALHA",
}

_STATUS_TAGS: dict[Status, str] = {
    Status.SUCCESS: "OK",
    Status.WARNING: "WARNING",
    Status.FAILED: "ERROR",
    Status.SKIPPED: "SKIPPED",
}


@dataclass
class ModuleResult:
    """Resultado de uma etapa da manutenção.

    ``summary`` é a mensagem curta exibida ao técnico (português); ``details``
    guarda dados estruturados usados em relatórios, histórico e JSON.
    """

    module: str
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def updates_available(self) -> int:
        return int(self.details.get("updates_available", 0) or 0)

    @property
    def tag(self) -> str:
        """Marcador exibido na CLI/relatório: OK, UPDATE, WARNING, ERROR ou SKIPPED."""
        if self.status is Status.WARNING and self.updates_available:
            return "UPDATE"
        return _STATUS_TAGS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "status": str(self.status),
            "summary": self.summary,
            "details": self.details,
            "errors": list(self.errors),
            "duration_seconds": round(self.duration_seconds, 3),
        }


def aggregate(
    results: Iterable[ModuleResult],
    *,
    requested_tasks: Collection[str],
    reboot_required: bool = False,
    critical_error: bool = False,
) -> OverallResult:
    """Calcula o resultado geral.

    - FAILED: erro crítico ou todas as tarefas solicitadas que foram executadas falharam;
    - SUCCESS_WITH_WARNINGS: qualquer WARNING/FAILED ou reinicialização pendente;
    - SUCCESS: caso contrário.

    Uma tarefa opcional que falha não derruba a manutenção inteira.
    """
    if critical_error:
        return OverallResult.FAILED
    results = list(results)
    executed_tasks = [
        r for r in results if r.module in requested_tasks and r.status is not Status.SKIPPED
    ]
    if executed_tasks and all(r.status is Status.FAILED for r in executed_tasks):
        return OverallResult.FAILED
    if reboot_required or any(r.status in (Status.WARNING, Status.FAILED) for r in results):
        return OverallResult.SUCCESS_WITH_WARNINGS
    return OverallResult.SUCCESS
