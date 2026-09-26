"""Cópia opcional de log e relatórios para um compartilhamento de rede.

Usa a identidade da conta que executa o agente (usuário ou conta do computador,
quando agendado como SYSTEM): nenhuma credencial é armazenada. Uma falha (servidor
offline, sem permissão, lentidão) nunca interrompe a manutenção e os arquivos locais
são sempre mantidos.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from maintenance.logging_service import safe_name
from maintenance.process import OperationTimeoutError, call_with_timeout
from maintenance.results import ModuleResult, Status

logger = logging.getLogger(__name__)

MODULE = "network_sync"


def sync_files(
    destination: str,
    hostname: str,
    files: Sequence[Path | None],
    *,
    timeout: float,
    copy: Callable[[Path, Path], Any] = shutil.copy2,
) -> ModuleResult:
    """Copia ``files`` para ``<destination>\\<HOSTNAME>\\``."""
    if not destination:
        return ModuleResult(MODULE, Status.SKIPPED, "Sincronização de rede não configurada")
    target = Path(destination) / safe_name(hostname)
    existing = [path for path in files if path is not None and path.is_file()]
    logger.info("Synchronizing %d file(s) to network location", len(existing))

    def copy_all() -> list[str]:
        target.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for source in existing:
            copy(source, target / source.name)
            copied.append(source.name)
        return copied

    try:
        copied = call_with_timeout(copy_all, timeout, name="network-sync")
    except OperationTimeoutError:
        reason = f"tempo limite de {timeout:.0f}s excedido"
    except OSError as exc:
        reason = exc.strerror or str(exc)
    else:
        logger.info("Network log synchronization completed: %d file(s) copied", len(copied))
        return ModuleResult(
            MODULE,
            Status.SUCCESS,
            f"{len(copied)} arquivo(s) copiado(s) para {target}",
            {"destination": str(target), "files": copied},
        )
    logger.warning("Network log synchronization failed. %s. Local files were kept.", reason)
    return ModuleResult(
        MODULE,
        Status.WARNING,
        f"Falha na sincronização com {target}: {reason}. Arquivos mantidos localmente.",
        {"destination": str(target), "files": []},
        errors=[reason],
    )
