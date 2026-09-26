"""Formatação de tamanhos, durações e datas para exibição."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(size: float | int | None) -> str:
    """Formata bytes em base 1024 (como o Explorer): ``2.1 GB``, ``512 MB``, ``0 B``."""
    if size is None:
        return "-"
    value = float(max(size, 0))
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("inalcançável")


def format_duration(seconds: float | None, *, compact: bool = False) -> str:
    """``14m 32s`` (relatório) ou ``14m32s`` (histórico, com minutos em dois dígitos)."""
    if seconds is None:
        return "-"
    total = int(round(max(seconds, 0)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if compact:
        if hours:
            return f"{hours}h{minutes:02d}m{secs:02d}s"
        return f"{minutes:02d}m{secs:02d}s"
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "desconhecido"
    total_minutes = int(max(seconds, 0) // 60)
    days, rest = divmod(total_minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_datetime(value: datetime | None, *, seconds: bool = False) -> str:
    """Data no padrão brasileiro: ``26/09/2026 08:32``."""
    if value is None:
        return "-"
    return value.strftime("%d/%m/%Y %H:%M:%S" if seconds else "%d/%m/%Y %H:%M")


def yes_no(value: bool) -> str:
    return "Sim" if value else "Não"
