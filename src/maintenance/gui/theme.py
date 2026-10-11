"""Tema escuro: paleta, fontes, ícones (fontes de símbolos do Windows) e estilos ttk."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Paleta: preto e grafite como base; verde, amarelo e vermelho apenas para estados.
BG = "#0e1013"
SIDEBAR = "#121418"
SURFACE = "#191c21"
SURFACE_HOVER = "#21252b"
SURFACE_ACTIVE = "#272c33"
SURFACE_SUNKEN = "#14171b"
BORDER = "#272b31"
BORDER_STRONG = "#363c44"
TEXT = "#e6e8eb"
TEXT_MUTED = "#a2a9b1"
TEXT_FAINT = "#6c737c"
GREEN = "#3fb950"
GREEN_BUTTON = "#238636"
GREEN_BUTTON_HOVER = "#2ea043"
GREEN_BG = "#10241a"
YELLOW = "#d29922"
YELLOW_BG = "#2a2112"
RED = "#f85149"
RED_BG = "#2c1517"
NEUTRAL = "#8b949e"
NEUTRAL_BG = "#1d2127"
FOCUS = "#8b949e"

# Nível visual -> (cor principal, fundo tingido)
LEVEL_COLORS: dict[str, tuple[str, str]] = {
    "ok": (GREEN, GREEN_BG),
    "warning": (YELLOW, YELLOW_BG),
    "error": (RED, RED_BG),
    "info": (NEUTRAL, NEUTRAL_BG),
    "skipped": (TEXT_FAINT, NEUTRAL_BG),
}


class Glyph:
    """Símbolos das fontes Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10)."""

    HOME = ""
    DIAGNOSTIC = ""
    WRENCH = ""
    HISTORY = ""
    SHIELD = ""
    ADMIN = ""
    REFRESH = ""
    PLAY = ""
    SEARCH = ""
    PC = ""
    MONITOR = ""
    USER = ""
    WINDOWS = ""
    CPU = ""
    MEMORY = ""
    DISK = ""
    CLOCK = ""
    PACKAGE = ""
    GLOBE = ""
    RESTART = ""
    SYNC = ""
    TRASH = ""
    FOLDER = ""
    DOCUMENT = ""
    LOG = ""
    LIGHTBULB = ""
    ARROW = ""
    OK = ""
    WARNING = ""
    ERROR = ""
    INFO = ""
    SKIPPED = ""
    QUESTION = ""
    CHECK = ""


LEVEL_GLYPHS: dict[str, str] = {
    "ok": Glyph.OK,
    "warning": Glyph.WARNING,
    "error": Glyph.ERROR,
    "info": Glyph.INFO,
    "skipped": Glyph.SKIPPED,
}

_ICON_FAMILIES = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
_scale = 1.0


def px(value: float) -> int:
    """Converte pixels de projeto (96 DPI) para pixels reais da tela."""
    return max(int(round(value * _scale)), 1) if value > 0 else 0


class Fonts:
    """Fontes nomeadas da interface (criadas após a janela principal)."""

    def __init__(self, root: tk.Misc) -> None:
        families = set(tkfont.families(root))
        base = "Segoe UI" if "Segoe UI" in families else "TkDefaultFont"
        strong = "Segoe UI Semibold" if "Segoe UI Semibold" in families else base
        self.icon_family = next((name for name in _ICON_FAMILIES if name in families), None)

        def font(family: str, size: int) -> tkfont.Font:
            return tkfont.Font(root, family=family, size=size)

        self.body = font(base, 10)
        self.small = font(base, 9)
        self.strong = font(strong, 10)
        self.h1 = font(strong, 17)
        self.h2 = font(strong, 13)
        self.h3 = font(strong, 11)
        self.value = font(strong, 12)
        self.mono = font("Consolas" if "Consolas" in families else "TkFixedFont", 9)
        icon = self.icon_family or base
        self.icon_small = font(icon, 10)
        self.icon = font(icon, 12)
        self.icon_medium = font(icon, 16)
        self.icon_large = font(icon, 24)

    def glyph(self, value: str) -> str:
        """Sem fonte de símbolos (caso raro), os ícones são omitidos."""
        return value if self.icon_family else ""


def init(root: tk.Tk) -> Fonts:
    """Calcula a escala de DPI, cria as fontes e configura os estilos ttk."""
    global _scale
    try:
        _scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
    except tk.TclError:
        _scale = 1.0
    root.configure(bg=BG)
    fonts = Fonts(root)
    _configure_styles(root, fonts)
    return fonts


def _configure_styles(root: tk.Tk, fonts: Fonts) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        fieldbackground=SURFACE,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        troughcolor=BG,
        focuscolor=FOCUS,
        font=fonts.body,
    )
    # Barra de rolagem fina, sem setas.
    for orient in ("Vertical", "Horizontal"):
        name = f"Dark.{orient}.TScrollbar"
        style.layout(
            name,
            [
                (
                    f"{orient}.Scrollbar.trough",
                    {
                        "sticky": "ns" if orient == "Vertical" else "ew",
                        "children": [
                            (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                        ],
                    },
                )
            ],
        )
        style.configure(
            name,
            troughcolor=BG,
            background=BORDER_STRONG,
            bordercolor=BG,
            lightcolor=BORDER_STRONG,
            darkcolor=BORDER_STRONG,
            arrowsize=px(9),
            gripcount=0,
        )
        style.map(
            name,
            background=[("pressed", TEXT_FAINT), ("active", TEXT_FAINT)],
            lightcolor=[("pressed", TEXT_FAINT), ("active", TEXT_FAINT)],
            darkcolor=[("pressed", TEXT_FAINT), ("active", TEXT_FAINT)],
        )
    # Tabela do histórico.
    style.layout("History.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure(
        "History.Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=SURFACE,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        rowheight=px(30),
        font=fonts.body,
    )
    style.map(
        "History.Treeview",
        background=[("selected", SURFACE_ACTIVE)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "History.Treeview.Heading",
        background=SURFACE_SUNKEN,
        foreground=TEXT_MUTED,
        bordercolor=BORDER,
        lightcolor=SURFACE_SUNKEN,
        darkcolor=SURFACE_SUNKEN,
        relief="flat",
        padding=(px(8), px(6)),
        font=fonts.strong,
    )
    style.map("History.Treeview.Heading", background=[("active", SURFACE_HOVER)])
