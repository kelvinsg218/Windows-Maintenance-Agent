"""Limpeza segura de arquivos temporários.

Garantias:
- apenas locais conhecidos, definidos no código (a configuração só os liga/desliga);
- cada raiz precisa ser exatamente ``<perfis>\\<usuário>\\AppData\\Local\\Temp`` ou
  ``<Windows>\\Temp`` — variáveis de ambiente como ``%TEMP%`` não são usadas como origem;
- links simbólicos, junctions e demais reparse points nunca são seguidos nem removidos;
- só remove arquivos mais antigos que a idade mínima, considerando a data de modificação
  e a de criação (arquivos recém-extraídos com data antiga ficam protegidos);
- arquivos bloqueados ou sem permissão são ignorados e contabilizados;
- a própria raiz nunca é removida e nomes de arquivos não são registrados (privacidade).
"""

from __future__ import annotations

import logging
import os
import stat
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from maintenance.config import CLEANUP_TARGET_NAMES
from maintenance.formatting import format_bytes
from maintenance.logging_service import SUCCESS
from maintenance.registry import HKLM, RegistryReader
from maintenance.results import ModuleResult, Status

logger = logging.getLogger(__name__)

MODULE = "cleanup"
TARGET_USER_TEMP = "user_temp"
TARGET_WINDOWS_TEMP = "windows_temp"
TARGET_ALL_PROFILES_TEMP = "all_profiles_temp"
ADMIN_TARGETS = frozenset({TARGET_WINDOWS_TEMP, TARGET_ALL_PROFILES_TEMP})
TARGET_LABELS = {
    TARGET_USER_TEMP: "Temp do usuário atual",
    TARGET_WINDOWS_TEMP: "Temp do Windows",
    TARGET_ALL_PROFILES_TEMP: "Temp de todos os perfis",
}
PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
LARGE_TEMP_BYTES = 1024**3  # acima de 1 GB o "check" sinaliza WARNING

_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_READONLY = 0x1  # FILE_ATTRIBUTE_READONLY


class UnsafePathError(ValueError):
    """Caminho recusado pelas regras de segurança da limpeza."""


@dataclass
class CleanupStats:
    files_scanned: int = 0
    files_removed: int = 0
    bytes_removed: int = 0
    files_skipped: int = 0
    skipped_recent: int = 0
    skipped_in_use: int = 0
    skipped_links: int = 0
    files_eligible: int = 0
    bytes_eligible: int = 0
    dirs_removed: int = 0
    scan_errors: int = 0

    def add(self, other: CleanupStats) -> None:
        for name, value in asdict(other).items():
            setattr(self, name, getattr(self, name) + value)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class CleanupTarget:
    name: str
    label: str
    paths: tuple[Path, ...]


@dataclass
class CleanupPlan:
    targets: list[CleanupTarget] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # locais não aplicáveis (informativo)
    warnings: list[str] = field(default_factory=list)  # locais recusados por segurança


def is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT) or stat.S_ISLNK(info.st_mode)


def newest_timestamp(info: os.stat_result) -> float:
    """Data mais recente entre modificação e criação do arquivo."""
    created = getattr(info, "st_birthtime", None) or info.st_ctime
    return max(info.st_mtime, created)


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def windows_directory() -> Path:
    """Pasta do Windows obtida pela API (não depende de variáveis de ambiente)."""
    if sys.platform == "win32":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            length = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, 260)
            if 0 < length < 260:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path(r"C:\Windows")


def _expand_system_drive(value: str, windows_dir: Path) -> Path:
    drive = PureWindowsPath(windows_dir).drive or "C:"
    lowered = value.lower()
    marker = "%systemdrive%"
    if lowered.startswith(marker):
        value = drive + value[len(marker) :]
    return Path(value)


def profiles_directory(registry: RegistryReader, windows_dir: Path) -> Path | None:
    value = registry.get_value(HKLM, PROFILE_LIST_KEY, "ProfilesDirectory")
    if isinstance(value, str) and value.strip():
        return _expand_system_drive(value.strip(), windows_dir)
    return None


def user_profile_paths(registry: RegistryReader, windows_dir: Path) -> list[Path]:
    """Perfis de contas de usuário (SIDs S-1-5-21-*); ignora contas de serviço e .bak."""
    profiles: list[Path] = []
    for sid in registry.list_subkeys(HKLM, PROFILE_LIST_KEY):
        if not sid.upper().startswith("S-1-5-21-") or sid.lower().endswith(".bak"):
            continue
        value = registry.get_value(HKLM, rf"{PROFILE_LIST_KEY}\{sid}", "ProfileImagePath")
        if isinstance(value, str) and value.strip():
            profiles.append(_expand_system_drive(value.strip(), windows_dir))
    return profiles


def _relative_parts(path: Path, base: Path) -> list[str] | None:
    try:
        relative = PureWindowsPath(_norm(path)).relative_to(PureWindowsPath(_norm(base)))
    except ValueError:
        return None
    return [part.lower() for part in relative.parts]


def _is_profile_temp(path: Path, profiles_dir: Path | None) -> bool:
    if profiles_dir is None:
        return False
    parts = _relative_parts(path, profiles_dir)
    return parts is not None and len(parts) == 4 and parts[1:] == ["appdata", "local", "temp"]


def _critical_directories(windows_dir: Path, profiles_dir: Path | None) -> set[str]:
    drive = PureWindowsPath(windows_dir).drive or "C:"
    critical = {
        drive + "\\",
        str(windows_dir),
        str(windows_dir / "System32"),
        str(windows_dir / "SysWOW64"),
        drive + r"\Program Files",
        drive + r"\Program Files (x86)",
        drive + r"\ProgramData",
    }
    if profiles_dir is not None:
        critical.add(str(profiles_dir))
    return {_norm(path) for path in critical}


def validate_root(path: Path, *, windows_dir: Path, profiles_dir: Path | None) -> Path:
    """Aceita somente ``<Windows>\\Temp`` ou ``<perfis>\\<usuário>\\AppData\\Local\\Temp``."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise UnsafePathError(f"pasta inexistente ou inacessível ({exc.strerror or exc})") from exc
    if is_reparse_point(info):
        raise UnsafePathError("a pasta é um link simbólico/junction")
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafePathError("o caminho não é uma pasta")
    resolved = Path(os.path.realpath(path))
    if _norm(resolved) in _critical_directories(windows_dir, profiles_dir):
        raise UnsafePathError("diretório crítico do sistema")
    if _norm(resolved) == _norm(windows_dir / "Temp") or _is_profile_temp(resolved, profiles_dir):
        return resolved
    raise UnsafePathError("local fora do padrão permitido")


def plan_targets(
    names: Iterable[str],
    *,
    is_admin: bool,
    registry: RegistryReader,
    env: Mapping[str, str] | None = None,
    windows_dir: Path | None = None,
) -> CleanupPlan:
    """Resolve os locais de limpeza habilitados, validando cada raiz."""
    env = os.environ if env is None else env
    windows_dir = windows_dir or windows_directory()
    profiles_dir = profiles_directory(registry, windows_dir)
    plan = CleanupPlan()
    seen: set[str] = set()

    def accept(candidates: list[Path], label: str) -> list[Path]:
        accepted: list[Path] = []
        rejected = 0
        for candidate in candidates:
            try:
                root = validate_root(candidate, windows_dir=windows_dir, profiles_dir=profiles_dir)
            except UnsafePathError as exc:
                rejected += 1
                logger.warning("Cleanup location rejected (%s): %s", label, exc)
                continue
            if _norm(root) not in seen:
                seen.add(_norm(root))
                accepted.append(root)
        if rejected:
            plan.warnings.append(f"{label}: {rejected} pasta(s) recusada(s) por segurança")
        return accepted

    for name in dict.fromkeys(names):
        if name not in CLEANUP_TARGET_NAMES:
            raise ValueError(f"local de limpeza desconhecido: {name}")
        label = TARGET_LABELS[name]
        if name in ADMIN_TARGETS and not is_admin:
            plan.notes.append(f"{label}: requer privilégios administrativos")
            continue
        if name == TARGET_USER_TEMP:
            profile = env.get("USERPROFILE", "")
            parts = (
                _relative_parts(Path(profile), profiles_dir) if profile and profiles_dir else None
            )
            if parts is None or len(parts) != 1:
                plan.notes.append(f"{label}: conta sem perfil de usuário padrão (ex.: SYSTEM)")
                continue
            candidates = [Path(profile, "AppData", "Local", "Temp")]
        elif name == TARGET_WINDOWS_TEMP:
            candidates = [windows_dir / "Temp"]
        else:
            candidates = [
                profile / "AppData" / "Local" / "Temp"
                for profile in user_profile_paths(registry, windows_dir)
            ]
            candidates = [path for path in candidates if path.is_dir()]
        paths = accept(candidates, label)
        if paths:
            plan.targets.append(CleanupTarget(name, label, tuple(paths)))
    return plan


def _inside(path: str, root: str) -> bool:
    return os.path.normcase(path).startswith(root.rstrip("\\/") + os.sep)


def _remove_file(path: str, info: os.stat_result, root: str) -> bool:
    if not _inside(path, root):  # defesa em profundidade: nunca sai da raiz
        return False
    try:
        os.unlink(path)
        return True
    except PermissionError:
        if not getattr(info, "st_file_attributes", 0) & _READONLY:
            return False  # em uso por outro processo ou sem permissão
        try:
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
            return True
        except OSError:
            try:
                os.chmod(path, stat.S_IREAD)  # restaura o atributo original
            except OSError:
                pass
            return False
    except OSError:
        return False


def clean_tree(
    root: Path, *, min_age_seconds: float, dry_run: bool, now: float | None = None
) -> CleanupStats:
    """Remove (ou, em dry-run, apenas contabiliza) arquivos antigos dentro de ``root``."""
    stats = CleanupStats()
    cutoff = (time.time() if now is None else now) - min_age_seconds
    root_text = os.path.normcase(str(root))
    directories: list[tuple[str, float]] = []
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            stats.scan_errors += 1
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                stats.scan_errors += 1
                continue
            if is_reparse_point(info):
                stats.skipped_links += 1
                stats.files_skipped += 1
                continue
            newest = newest_timestamp(info)
            if stat.S_ISDIR(info.st_mode):
                stack.append(entry.path)
                directories.append((entry.path, newest))
                continue
            stats.files_scanned += 1
            if newest > cutoff:
                stats.skipped_recent += 1
                stats.files_skipped += 1
                continue
            stats.files_eligible += 1
            stats.bytes_eligible += info.st_size
            if dry_run:
                continue
            if _remove_file(entry.path, info, root_text):
                stats.files_removed += 1
                stats.bytes_removed += info.st_size
            else:
                stats.skipped_in_use += 1
                stats.files_skipped += 1
    if not dry_run:
        # Pastas vazias antigas, das mais profundas para as mais rasas (a raiz é preservada).
        for path, newest in sorted(directories, key=lambda d: d[0].count(os.sep), reverse=True):
            if newest > cutoff or not _inside(path, root_text):
                continue
            try:
                os.rmdir(path)
            except OSError:
                continue
            stats.dirs_removed += 1
    return stats


def process_cleanup(
    names: Iterable[str],
    *,
    apply: bool,
    is_admin: bool,
    registry: RegistryReader,
    dry_run: bool = False,
    min_age_days: int = 7,
    env: Mapping[str, str] | None = None,
    windows_dir: Path | None = None,
    now: float | None = None,
) -> ModuleResult:
    """Estima (``apply=False`` ou dry-run) ou executa a limpeza dos locais habilitados."""
    logger.info("Checking temporary files" if not apply or dry_run else "Cleaning temporary files")
    simulate = not apply or dry_run
    plan = plan_targets(
        names, is_admin=is_admin, registry=registry, env=env, windows_dir=windows_dir
    )
    totals = CleanupStats()
    per_target: list[dict[str, Any]] = []
    for target in plan.targets:
        stats = CleanupStats()
        for path in target.paths:
            stats.add(
                clean_tree(path, min_age_seconds=min_age_days * 86400, dry_run=simulate, now=now)
            )
        totals.add(stats)
        per_target.append({"name": target.name, "label": target.label, **stats.to_dict()})
        logger.info(
            "%s (%d folder(s)): %d files scanned, %d eligible (%s), %d removed (%s), %d skipped",
            target.name,
            len(target.paths),
            stats.files_scanned,
            stats.files_eligible,
            format_bytes(stats.bytes_eligible),
            stats.files_removed,
            format_bytes(stats.bytes_removed),
            stats.files_skipped,
        )
    for note in plan.notes:
        logger.info("Cleanup location skipped: %s", note)

    details: dict[str, Any] = {
        **totals.to_dict(),
        "targets": per_target,
        "notes": plan.notes,
        "warnings": plan.warnings,
        "min_age_days": min_age_days,
        "dry_run": simulate,
        "cleanup_performed": apply and not dry_run and bool(plan.targets),
    }
    if not plan.targets:
        status = Status.WARNING if plan.warnings else Status.SKIPPED
        reason = "; ".join(plan.warnings + plan.notes) or "nenhum local habilitado"
        return ModuleResult(
            MODULE, status, f"Nenhum local de limpeza disponível ({reason})", details
        )

    if not apply:
        found = format_bytes(totals.bytes_eligible)
        if totals.bytes_eligible >= LARGE_TEMP_BYTES:
            return ModuleResult(
                MODULE, Status.WARNING, f"{found} de arquivos temporários encontrados", details
            )
        return ModuleResult(
            MODULE, Status.SUCCESS, f"{found} de arquivos temporários removíveis", details
        )
    if dry_run:
        return ModuleResult(
            MODULE,
            Status.SKIPPED,
            f"{format_bytes(totals.bytes_eligible)} seriam removidos (dry-run)",
            details,
        )
    logger.log(SUCCESS, "Cleanup finished: %s recovered", format_bytes(totals.bytes_removed))
    status = Status.WARNING if plan.warnings else Status.SUCCESS
    return ModuleResult(MODULE, status, f"{format_bytes(totals.bytes_removed)} removidos", details)
