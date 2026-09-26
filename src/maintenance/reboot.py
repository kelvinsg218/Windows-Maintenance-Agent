"""Detecção de reinicialização pendente.

O agente NUNCA reinicia o computador: apenas detecta, registra e informa.
A decisão de reiniciar é sempre do usuário/administrador.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from maintenance.registry import HKLM, RegistryReader
from maintenance.results import ModuleResult, Status

CBS_REBOOT_PENDING = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"
)
WU_REBOOT_REQUIRED = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
)
SESSION_MANAGER = r"SYSTEM\CurrentControlSet\Control\Session Manager"
UPDATES_KEY = r"SOFTWARE\Microsoft\Updates"
ACTIVE_COMPUTER_NAME = r"SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName"
PENDING_COMPUTER_NAME = r"SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName"

SOURCE_CBS = "Component Based Servicing"
SOURCE_WINDOWS_UPDATE = "Windows Update"
SOURCE_UPDATE_EXE = "UpdateExeVolatile"
SOURCE_FILE_RENAME = "PendingFileRenameOperations"
SOURCE_COMPUTER_RENAME = "Renomeação do computador"
SOURCE_INSTALLATION = "Instalação de atualizações nesta execução"

REBOOT_UPDATES_MESSAGE = "ATENÇÃO: existem atualizações que exigem reinicialização."
REBOOT_GENERIC_MESSAGE = "ATENÇÃO: existe uma reinicialização pendente no sistema."


@dataclass(frozen=True)
class RebootStatus:
    required: bool
    sources: tuple[str, ...] = ()
    update_related: bool = False
    pending_file_renames: int = 0
    pending_file_deletes: int = 0

    @property
    def message(self) -> str:
        if not self.required:
            return ""
        return REBOOT_UPDATES_MESSAGE if self.update_related else REBOOT_GENERIC_MESSAGE

    def with_installation_reboot(self, reboot_required: bool) -> RebootStatus:
        """Incorpora o resultado de uma instalação que pediu reinicialização."""
        if not reboot_required or SOURCE_INSTALLATION in self.sources:
            return self
        return replace(
            self, required=True, update_related=True, sources=self.sources + (SOURCE_INSTALLATION,)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reboot_required": self.required,
            "sources": list(self.sources),
            "update_related": self.update_related,
            "pending_file_renames": self.pending_file_renames,
            "pending_file_deletes": self.pending_file_deletes,
        }


def count_pending_file_operations(entries: Sequence[str] | None) -> tuple[int, int]:
    """Conta renomeações e exclusões em ``PendingFileRenameOperations``.

    O valor é uma lista de pares (origem, destino). Destino vazio = exclusão agendada,
    que não exige reinicialização por si só (fonte clássica de falso positivo).
    Destino preenchido = substituição de arquivo em uso, efetivada só ao reiniciar.
    """
    if not entries:
        return 0, 0
    renames = deletes = 0
    for index in range(0, len(entries) - 1, 2):
        source, destination = entries[index], entries[index + 1]
        if not source:
            continue
        if destination.strip():
            renames += 1
        else:
            deletes += 1
    if len(entries) % 2 == 1 and entries[-1]:
        deletes += 1
    return renames, deletes


def detect_pending_reboot(registry: RegistryReader) -> RebootStatus:
    sources: list[str] = []
    update_related = False

    if registry.key_exists(HKLM, CBS_REBOOT_PENDING):
        sources.append(SOURCE_CBS)
        update_related = True
    if registry.key_exists(HKLM, WU_REBOOT_REQUIRED):
        sources.append(SOURCE_WINDOWS_UPDATE)
        update_related = True
    volatile = registry.get_value(HKLM, UPDATES_KEY, "UpdateExeVolatile")
    if isinstance(volatile, int) and volatile != 0:
        sources.append(SOURCE_UPDATE_EXE)
        update_related = True

    renames = deletes = 0
    for value_name in ("PendingFileRenameOperations", "PendingFileRenameOperations2"):
        entries = registry.get_value(HKLM, SESSION_MANAGER, value_name)
        if isinstance(entries, list):
            value_renames, value_deletes = count_pending_file_operations(entries)
            renames += value_renames
            deletes += value_deletes
    if renames:
        sources.append(SOURCE_FILE_RENAME)

    active = registry.get_value(HKLM, ACTIVE_COMPUTER_NAME, "ComputerName")
    pending = registry.get_value(HKLM, PENDING_COMPUTER_NAME, "ComputerName")
    if isinstance(active, str) and isinstance(pending, str) and active.upper() != pending.upper():
        sources.append(SOURCE_COMPUTER_RENAME)

    return RebootStatus(
        required=bool(sources),
        sources=tuple(sources),
        update_related=update_related,
        pending_file_renames=renames,
        pending_file_deletes=deletes,
    )


def reboot_result(status: RebootStatus) -> ModuleResult:
    details = status.to_dict()
    if status.required:
        return ModuleResult(
            "reboot",
            Status.WARNING,
            f"Reinicialização necessária ({', '.join(status.sources)})",
            details,
        )
    summary = "Nenhuma reinicialização pendente"
    if status.pending_file_deletes:
        summary += (
            f" ({status.pending_file_deletes} exclusões de arquivos agendadas para a próxima "
            "inicialização, sem exigir reinício)"
        )
    return ModuleResult("reboot", Status.SUCCESS, summary, details)
