"""Carregamento e validação da configuração do agente (arquivo JSON)."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_FILE_NAME = "config.json"

# Locais de limpeza conhecidos. A configuração só pode ligar/desligar estes nomes:
# nenhum caminho arbitrário de limpeza é aceito de fora do código.
CLEANUP_TARGET_NAMES: tuple[str, ...] = ("user_temp", "windows_temp", "all_profiles_temp")

_SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|pwd|senha|secret|token|credential|api[_-]?key", re.IGNORECASE
)
_BAD = re.escape('/:*?"<>|')  # caracteres inválidos em caminhos Windows
_UNC_PATH = re.compile(rf"^\\\\[^\\{_BAD}]+\\[^\\{_BAD}]+(\\[^{_BAD}]*)*$")
_DRIVE_PATH = re.compile(rf"^[A-Za-z]:\\[^{_BAD}]*$")


class ConfigError(ValueError):
    """Configuração inválida. É um erro crítico: a manutenção não é iniciada."""


@dataclass(frozen=True)
class AppConfig:
    update_chrome: bool = True
    update_windows: bool = True
    cleanup_temp: bool = True
    network_log_path: str = ""
    log_retention_days: int = 90
    data_dir: str = ""
    winget_timeout_seconds: int = 900
    windows_update_search_timeout_minutes: int = 30
    windows_update_install_timeout_minutes: int = 180
    windows_update_include_optional: bool = False
    windows_update_accept_eulas: bool = False
    cleanup_targets: tuple[str, ...] = CLEANUP_TARGET_NAMES
    cleanup_min_age_days: int = 7
    disk_min_free_percent: int = 10
    disk_min_free_gb: int = 10
    network_timeout_seconds: int = 120


_INT_RANGES: dict[str, tuple[int, int]] = {
    "log_retention_days": (0, 3650),
    "winget_timeout_seconds": (60, 7200),
    "windows_update_search_timeout_minutes": (1, 240),
    "windows_update_install_timeout_minutes": (5, 720),
    "cleanup_min_age_days": (1, 365),
    "disk_min_free_percent": (0, 90),
    "disk_min_free_gb": (0, 100_000),
    "network_timeout_seconds": (5, 3600),
}


@dataclass(frozen=True)
class LoadedConfig:
    config: AppConfig
    path: Path | None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppPaths:
    """Diretórios de trabalho do agente (logs, relatórios e dados locais)."""

    base_dir: Path
    logs_dir: Path
    reports_dir: Path
    data_dir: Path

    @classmethod
    def from_base(cls, base: Path) -> AppPaths:
        return cls(base, base / "logs", base / "reports", base / "data")

    @property
    def history_db(self) -> Path:
        return self.data_dir / "history.db"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "maintenance.lock"

    def ensure(self) -> None:
        for directory in (self.logs_dir, self.reports_dir, self.data_dir):
            directory.mkdir(parents=True, exist_ok=True)


def app_home() -> Path:
    """Diretório base do agente.

    - executável (PyInstaller): pasta do ``maintenance.exe``;
    - código-fonte: raiz do projeto (onde está o ``pyproject.toml``);
    - pacote instalado fora do projeto: ``%ProgramData%\\WindowsMaintenanceAgent``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    program_data = os.environ.get("ProgramData") or r"C:\ProgramData"
    return Path(program_data) / "WindowsMaintenanceAgent"


def default_config_path() -> Path:
    return app_home() / CONFIG_FILE_NAME


def resolve_paths(config: AppConfig) -> AppPaths:
    base = Path(config.data_dir) if config.data_dir else app_home()
    return AppPaths.from_base(base)


def load_config(path: Path | None = None) -> LoadedConfig:
    """Carrega a configuração. Sem ``path``, usa ``config.json`` do diretório do agente
    (ausente = valores padrão). Um ``path`` explícito inexistente é erro."""
    explicit = path is not None
    config_path = Path(path) if explicit else default_config_path()
    if not config_path.is_file():
        if explicit:
            raise ConfigError(f"Arquivo de configuração não encontrado: {config_path}")
        return LoadedConfig(
            AppConfig(), None, [f"{CONFIG_FILE_NAME} não encontrado; usando configuração padrão."]
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ConfigError(f"Não foi possível ler {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"JSON inválido em {config_path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    config, notes = parse_config(data)
    return LoadedConfig(config, config_path, notes)


def parse_config(data: Any) -> tuple[AppConfig, list[str]]:
    """Valida um objeto JSON já decodificado. Chaves iniciadas por ``_`` são comentários."""
    if not isinstance(data, dict):
        raise ConfigError("A configuração deve ser um objeto JSON ({ ... }).")
    known = {f.name for f in fields(AppConfig)}
    defaults = AppConfig()
    values: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if _SENSITIVE_KEY.search(key):
            raise ConfigError(
                f"A chave '{key}' parece conter credenciais. Credenciais não devem ser "
                "armazenadas na configuração: o agente usa a identidade da conta que o executa."
            )
        if key not in known:
            notes.append(f"Chave de configuração desconhecida ignorada: '{key}'.")
            continue
        values[key] = _validate(key, value, getattr(defaults, key))
    return AppConfig(**values), notes


def _validate(key: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"'{key}' deve ser true ou false.")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"'{key}' deve ser um número inteiro.")
        low, high = _INT_RANGES[key]
        if not low <= value <= high:
            raise ConfigError(f"'{key}' deve estar entre {low} e {high} (recebido: {value}).")
        return value
    if key == "cleanup_targets":
        return _validate_cleanup_targets(value)
    if key == "network_log_path":
        return _validate_network_path(value)
    if key == "data_dir":
        return _validate_data_dir(value)
    raise AssertionError(f"campo sem validação: {key}")  # pragma: no cover


def _validate_cleanup_targets(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError("'cleanup_targets' deve ser uma lista de nomes.")
    unknown = [item for item in value if item not in CLEANUP_TARGET_NAMES]
    if unknown:
        raise ConfigError(
            f"'cleanup_targets' contém locais desconhecidos: {', '.join(unknown)}. "
            f"Permitidos: {', '.join(CLEANUP_TARGET_NAMES)}."
        )
    return tuple(dict.fromkeys(value))


def _has_parent_reference(path: str) -> bool:
    return any(part == ".." for part in path.replace("/", "\\").split("\\"))


def _validate_network_path(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("'network_log_path' deve ser texto.")
    path = value.strip()
    if not path:
        return ""
    if not (_UNC_PATH.match(path) or _DRIVE_PATH.match(path)) or _has_parent_reference(path):
        raise ConfigError(
            "'network_log_path' deve ser um caminho absoluto, por exemplo "
            r"\\SERVIDOR\TI\maintenance (sem usuário/senha e sem '..')."
        )
    return path.rstrip("\\")


def _validate_data_dir(value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError("'data_dir' deve ser texto.")
    path = value.strip()
    if not path:
        return ""
    if not _DRIVE_PATH.match(path) or _has_parent_reference(path):
        raise ConfigError(
            r"'data_dir' deve ser um caminho local absoluto (ex.: C:\ProgramData\WMA)."
        )
    return path
