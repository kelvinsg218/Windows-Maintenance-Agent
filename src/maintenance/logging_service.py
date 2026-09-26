"""Logging do agente: um arquivo por execução, saída no console e redação de segredos.

Formato: ``2026-09-26 08:00:01 | INFO | Maintenance started``.
O agente não registra senhas, tokens, credenciais nem nomes de arquivos pessoais;
o filtro de redação é uma camada extra de proteção.
"""

from __future__ import annotations

import logging
import re
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")

LOGGER_NAME = "maintenance"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
FILE_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
CONSOLE_DATE_FORMAT = "%H:%M:%S"

_SENSITIVE_NAMES = r"password|passwd|pwd|senha|secret|token|api[_-]?key|client[_-]?secret"
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(rf"(?i)\b({_SENSITIVE_NAMES})(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
        r"\1\2***",
    ),
    (re.compile(r"(?i)\b(authorization\s*:\s*)(\w+\s+)?\S+"), r"\1***"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),
)
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Mascara valores com aparência de segredo antes de gravar o registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure_logging(
    log_file: Path | None = None, *, console: bool = True, verbose: bool = False
) -> logging.Logger:
    """(Re)configura o logger ``maintenance`` para uma execução."""
    logger = get_logger()
    close_logging()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    level = logging.DEBUG if verbose else logging.INFO
    redactor = RedactingFilter()
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, FILE_DATE_FORMAT))
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT, CONSOLE_DATE_FORMAT))
        console_handler.addFilter(redactor)
        logger.addHandler(console_handler)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def flush_logging() -> None:
    for handler in get_logger().handlers:
        handler.flush()


def close_logging() -> None:
    logger = get_logger()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def safe_name(value: str) -> str:
    return _UNSAFE_NAME_CHARS.sub("_", value).strip("._") or "UNKNOWN"


def run_file_stem(hostname: str, started_at: datetime, suffix: str = "") -> str:
    """Nome base dos arquivos de uma execução: ``TI-04_2026-09-26_080001``."""
    return f"{safe_name(hostname)}_{started_at:%Y-%m-%d_%H%M%S}{suffix}"


def purge_old_files(
    directory: Path,
    *,
    retention_days: int,
    suffixes: tuple[str, ...] = (".log", ".txt", ".json"),
    now: float | None = None,
) -> int:
    """Remove arquivos antigos (não recursivo) apenas dos diretórios do próprio agente."""
    if retention_days <= 0 or not directory.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = 0
    for entry in directory.iterdir():
        try:
            if entry.suffix.lower() not in suffixes:
                continue
            info = entry.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff:
                continue
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed
