"""Verificação e atualização do Google Chrome via winget.

Somente pacotes do canal estável são considerados, com IDs fixos no código:
``Google.Chrome`` (instalador MSI Enterprise) e ``Google.Chrome.EXE`` (instalador EXE).
O Chrome instalado pelo instalador EXE não é correlacionado pelo winget ao ID
``Google.Chrome`` — e uma busca por nome pode correlacioná-lo ao pacote Beta —,
por isso os dois IDs estáveis são consultados explicitamente e nunca se busca por nome.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maintenance.logging_service import SUCCESS
from maintenance.process import CommandRunner, format_exit_code, run_command
from maintenance.registry import HKCU, HKLM, RegistryReader
from maintenance.results import ModuleResult, Status
from maintenance.winget import (
    REBOOT_REQUIRED_TO_FINISH,
    UPDATE_NOT_APPLICABLE,
    WingetError,
    WingetPackage,
    describe_exit_code,
    list_package,
    output_tail,
    upgrade_package,
)

logger = logging.getLogger(__name__)

MODULE = "chrome"
CHROME_STABLE_IDS: tuple[str, ...] = ("Google.Chrome", "Google.Chrome.EXE")

APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
UNINSTALL_KEYS: tuple[tuple[str, str], ...] = (
    (HKLM, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome"),
    (HKLM, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome"),
    (HKCU, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome"),
)
BEACON_KEY = r"SOFTWARE\Google\Chrome\BLBeacon"

SCOPE_MACHINE = "machine"
SCOPE_USER = "user"
_SCOPE_BY_HIVE = {HKLM: SCOPE_MACHINE, HKCU: SCOPE_USER}
# Canal estável: ...\Google\Chrome\Application\chrome.exe (Beta/Dev/Canary usam outras pastas).
_STABLE_PATH = re.compile(r"[\\/]Google[\\/]Chrome[\\/]Application[\\/]chrome\.exe$", re.I)


@dataclass(frozen=True)
class ChromeInstallation:
    installed: bool
    version: str | None = None
    path: str | None = None
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version": self.version,
            "path": self.path,
            "scope": self.scope,
        }


def detect_chrome(
    registry: RegistryReader, exists: Callable[[str], bool] = os.path.isfile
) -> ChromeInstallation:
    """Detecta o Chrome estável pelo Registro, confirmando que o executável existe."""
    path: str | None = None
    scope: str | None = None
    for hive in (HKLM, HKCU):
        value = registry.get_value(hive, APP_PATHS_KEY, "")
        candidate = value.strip().strip('"') if isinstance(value, str) else ""
        if candidate and _STABLE_PATH.search(candidate) and exists(candidate):
            path, scope = candidate, _SCOPE_BY_HIVE[hive]
            break

    version: str | None = None
    for hive, key in UNINSTALL_KEYS:
        display_version = registry.get_value(hive, key, "DisplayVersion")
        if not (isinstance(display_version, str) and display_version.strip()):
            continue
        version = display_version.strip()
        if path is None:
            location = registry.get_value(hive, key, "InstallLocation")
            if isinstance(location, str) and location.strip():
                candidate = str(Path(location.strip().strip('"'), "chrome.exe"))
                if _STABLE_PATH.search(candidate) and exists(candidate):
                    path, scope = candidate, _SCOPE_BY_HIVE[hive]
        break

    if path is None:
        return ChromeInstallation(installed=False)
    if version is None:
        beacon = registry.get_value(HKCU, BEACON_KEY, "version")
        version = beacon.strip() if isinstance(beacon, str) and beacon.strip() else None
    return ChromeInstallation(True, version, path, scope)


def _find_stable_package(
    winget: Path, *, timeout: float, runner: CommandRunner
) -> WingetPackage | None:
    for package_id in CHROME_STABLE_IDS:
        package = list_package(winget, package_id, timeout=timeout, runner=runner)
        if package is not None:
            logger.info(
                "Chrome winget package: %s (installed %s, available %s)",
                package.package_id,
                package.version,
                package.available or "-",
            )
            return package
    return None


def process_chrome(
    registry: RegistryReader,
    winget: Path | None,
    *,
    apply: bool,
    is_admin: bool,
    dry_run: bool = False,
    timeout: float = 900,
    runner: CommandRunner = run_command,
) -> ModuleResult:
    """Verifica (``apply=False``) ou atualiza (``apply=True``) o Google Chrome."""
    logger.info("Checking Chrome")
    installation = detect_chrome(registry)
    details: dict[str, Any] = installation.to_dict()
    if not installation.installed:
        logger.info("Chrome is not installed")
        return ModuleResult(MODULE, Status.SKIPPED, "Google Chrome não está instalado", details)
    logger.info("Chrome installed: version %s (%s scope)", installation.version, installation.scope)
    if winget is None:
        logger.warning("Winget not available: Chrome cannot be checked or updated")
        return ModuleResult(
            MODULE,
            Status.WARNING,
            "Winget indisponível: não foi possível verificar atualizações do Chrome",
            details,
        )

    try:
        package = _find_stable_package(winget, timeout=timeout, runner=runner)
    except WingetError as exc:
        logger.error("Chrome check failed: %s", exc)
        return ModuleResult(
            MODULE, Status.FAILED, "Falha ao consultar o winget", details, errors=[str(exc)]
        )
    if package is None:
        logger.warning("Chrome is not associated with a stable winget package")
        return ModuleResult(
            MODULE,
            Status.WARNING,
            "Chrome instalado, mas não associado a um pacote estável do winget "
            "(Google.Chrome / Google.Chrome.EXE)",
            details,
        )

    details.update(package_id=package.package_id, version_available=package.available)
    if not package.available:
        logger.info("Chrome is up to date")
        return ModuleResult(
            MODULE,
            Status.SUCCESS,
            f"Google Chrome atualizado ({package.version})",
            {**details, "updated": False},
        )

    change = f"{package.version} -> {package.available}"
    details["updates_available"] = 1
    logger.info("Chrome update available: %s", change)
    if not apply:
        return ModuleResult(
            MODULE, Status.WARNING, f"Google Chrome possui atualização ({change})", details
        )
    if dry_run:
        return ModuleResult(
            MODULE,
            Status.WARNING,
            f"Atualização disponível ({change}); não aplicada (dry-run)",
            details,
        )
    if installation.scope == SCOPE_MACHINE and not is_admin:
        logger.warning("Chrome update requires administrative privileges (machine-wide install)")
        return ModuleResult(
            MODULE,
            Status.WARNING,
            f"Atualização disponível ({change}), mas o Chrome está instalado para todos os "
            "usuários: requer privilégios administrativos",
            details,
        )
    return _upgrade(registry, winget, package, details, timeout=timeout, runner=runner)


def _upgrade(
    registry: RegistryReader,
    winget: Path,
    package: WingetPackage,
    details: dict[str, Any],
    *,
    timeout: float,
    runner: CommandRunner,
) -> ModuleResult:
    logger.info("Updating Chrome via winget (%s)", package.package_id)
    result = upgrade_package(winget, package.package_id, timeout=timeout, runner=runner)
    details["command"] = {
        "exit_code": format_exit_code(result.exit_code),
        "duration_seconds": round(result.duration_seconds, 1),
        "timed_out": result.timed_out,
    }
    if result.timed_out or result.error:
        logger.error("Chrome update failed: %s", result.describe_exit())
        return ModuleResult(
            MODULE,
            Status.FAILED,
            "Falha ao atualizar o Chrome: " + result.describe_exit(),
            {**details, "updated": False},
            errors=[result.describe_exit()],
            duration_seconds=result.duration_seconds,
        )
    if result.exit_code == UPDATE_NOT_APPLICABLE:
        logger.info("No applicable Chrome update found by winget")
        return ModuleResult(
            MODULE,
            Status.SUCCESS,
            "Nenhuma atualização aplicável do Chrome (já atualizado)",
            {**details, "updates_available": 0, "updated": False},
            duration_seconds=result.duration_seconds,
        )
    if result.exit_code not in (0, REBOOT_REQUIRED_TO_FINISH):
        message = describe_exit_code(result.exit_code)
        logger.error("Chrome update failed: %s | %s", message, output_tail(result))
        return ModuleResult(
            MODULE,
            Status.FAILED,
            f"Falha ao atualizar o Chrome: {message}",
            {**details, "updated": False},
            errors=[message],
            duration_seconds=result.duration_seconds,
        )

    version_after = detect_chrome(registry).version
    summary = f"Atualizado de {package.version} para {package.available}"
    if version_after and version_after != package.available:
        # Com o Chrome aberto, o instalador prepara a nova versão e a aplica no reinício.
        summary += " (a nova versão será aplicada quando o Chrome for reiniciado)"
    reboot_required = result.exit_code == REBOOT_REQUIRED_TO_FINISH
    if reboot_required:
        summary += "; reinicialização necessária para concluir"
    logger.log(SUCCESS, "Chrome updated: %s -> %s", package.version, package.available)
    return ModuleResult(
        MODULE,
        Status.SUCCESS,
        summary,
        {
            **details,
            "updates_available": 0,
            "updated": True,
            "version_after": version_after,
            "reboot_required": reboot_required,
        },
        duration_seconds=result.duration_seconds,
    )
