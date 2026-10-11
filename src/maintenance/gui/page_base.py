"""Base das páginas (cabeçalho fixo + conteúdo com rolagem) e painel de progresso."""

from __future__ import annotations

import time
import tkinter as tk
from typing import TYPE_CHECKING

from maintenance.gui import texts, theme
from maintenance.gui.theme import Fonts, px
from maintenance.gui.widgets import Card, ProgressBar, ScrollFrame, Spinner, WrapLabel

if TYPE_CHECKING:
    from maintenance.gui.app import MaintenanceApp


class Page(tk.Frame):
    name = ""

    def __init__(self, parent: tk.Misc, app: MaintenanceApp) -> None:
        super().__init__(parent, bg=theme.BG)
        self.app = app
        self.fonts = app.fonts
        header = tk.Frame(self, bg=theme.BG)
        header.pack(fill="x", padx=px(32), pady=(px(24), px(16)))
        titles = tk.Frame(header, bg=theme.BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(
            titles,
            text=texts.PAGE_TITLES[self.name],
            font=self.fonts.h1,
            fg=theme.TEXT,
            bg=theme.BG,
            anchor="w",
        ).pack(fill="x")
        WrapLabel(
            titles, text=texts.PAGE_SUBTITLES[self.name], font=self.fonts.body, fg=theme.TEXT_MUTED
        ).pack(fill="x", pady=(px(2), 0))
        self.header_actions = tk.Frame(header, bg=theme.BG)
        self.header_actions.pack(side="right", anchor="n", padx=(px(16), 0), pady=(px(4), 0))
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.content = tk.Frame(self.scroll.body, bg=theme.BG)
        self.content.pack(fill="both", expand=True, padx=px(32), pady=(0, px(28)))

    def on_show(self) -> None:
        """Chamado sempre que a página é exibida."""

    def refresh(self) -> None:
        """Atualização manual (F5)."""

    def on_busy_changed(self) -> None:
        """Uma operação (verificação ou manutenção) começou ou terminou."""


class ProgressPanel(Card):
    """Andamento de uma operação: indicador animado, etapa atual, tempo decorrido e barra."""

    def __init__(self, parent: tk.Misc, fonts: Fonts) -> None:
        super().__init__(parent)
        body = self.body
        top = tk.Frame(body, bg=theme.SURFACE)
        top.pack(fill="x")
        self._spinner = Spinner(top)
        self._spinner.pack(side="left", padx=(0, px(10)))
        self._title = tk.Label(top, font=fonts.strong, fg=theme.TEXT, bg=theme.SURFACE)
        self._title.pack(side="left")
        self._elapsed = tk.Label(top, font=fonts.small, fg=theme.TEXT_MUTED, bg=theme.SURFACE)
        self._elapsed.pack(side="right")
        self._step = WrapLabel(body, font=fonts.body, fg=theme.TEXT_MUTED)
        self._step.pack(fill="x", pady=(px(8), px(10)))
        self._bar = ProgressBar(body)
        self._bar.pack(fill="x")
        self._hint = WrapLabel(body, font=fonts.small, fg=theme.TEXT_FAINT)
        self._hint.pack(fill="x", pady=(px(10), 0))
        self._started = 0.0
        self._timer: str | None = None
        self.bind("<Destroy>", lambda _event: self._stop_timer(), add="+")

    def begin(self, title: str, hint: str = "") -> None:
        self._title.configure(text=title)
        self._hint.configure(text=hint)
        self._step.configure(text="Preparando...")
        self._bar.set_color(theme.GREEN)
        self._bar.set(0, animate=False)
        self._started = time.monotonic()
        self._spinner.start()
        self._stop_timer()
        self._tick()

    def set_step(self, text: str) -> None:
        self._step.configure(text=text)

    def set_fraction(self, fraction: float) -> None:
        self._bar.set(fraction)

    def finish(self) -> float:
        """Encerra o acompanhamento e oculta o painel (o resultado é exibido no resumo).

        Retorna a duração da operação, em segundos.
        """
        self._spinner.stop()
        self._stop_timer()
        self.pack_forget()
        return time.monotonic() - self._started

    def _update_elapsed(self) -> None:
        seconds = int(time.monotonic() - self._started)
        self._elapsed.configure(text=f"Tempo decorrido: {seconds // 60:02d}:{seconds % 60:02d}")

    def _tick(self) -> None:
        self._update_elapsed()
        self._timer = self.after(1000, self._tick)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None
