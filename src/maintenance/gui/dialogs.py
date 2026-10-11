"""Caixas de diálogo modais no tema escuro (confirmações, avisos e erros)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Sequence

from maintenance.gui import theme, winapi
from maintenance.gui.theme import Fonts, px
from maintenance.gui.widgets import Button, LevelIcon, WrapLabel

_KIND_LEVELS = {
    "info": "info",
    "question": "info",
    "success": "ok",
    "warning": "warning",
    "error": "error",
}


class MessageDialog(tk.Toplevel):
    """Diálogo modal. ``show`` retorna o índice do botão escolhido (ou ``cancel``)."""

    def __init__(
        self,
        parent: tk.Misc,
        fonts: Fonts,
        title: str,
        message: str,
        *,
        kind: str = "info",
        buttons: Sequence[tuple[str, str]] = (("OK", "primary"),),
        default: int = 0,
        cancel: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(parent, bg=theme.SURFACE)
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.result = cancel if cancel is not None else default
        self._cancel = cancel
        self._default = default

        body = tk.Frame(self, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, padx=px(24), pady=(px(22), px(16)))
        icon = LevelIcon(body, fonts, _KIND_LEVELS.get(kind, "info"), size="large")
        if kind == "question":
            icon.configure(text=fonts.glyph(theme.Glyph.QUESTION), fg=theme.TEXT_MUTED)
        icon.grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, px(16)))
        tk.Label(body, text=title, font=fonts.h2, fg=theme.TEXT, bg=theme.SURFACE, anchor="w").grid(
            row=0, column=1, sticky="ew"
        )
        WrapLabel(
            body, text=message, font=fonts.body, fg=theme.TEXT_MUTED, wraplength=px(420)
        ).grid(row=1, column=1, sticky="ew", pady=(px(8), 0))
        if detail:
            text = tk.Text(
                body,
                height=7,
                width=60,
                font=fonts.mono,
                bg=theme.SURFACE_SUNKEN,
                fg=theme.TEXT_MUTED,
                relief="flat",
                highlightthickness=1,
                highlightbackground=theme.BORDER,
                wrap="word",
                padx=px(8),
                pady=px(6),
            )
            text.insert("1.0", detail)
            text.configure(state="disabled")
            text.grid(row=2, column=1, sticky="ew", pady=(px(12), 0))
        body.grid_columnconfigure(1, weight=1)

        bar = tk.Frame(self, bg=theme.SURFACE_SUNKEN)
        bar.pack(fill="x")
        row = tk.Frame(bar, bg=theme.SURFACE_SUNKEN)
        row.pack(side="right", padx=px(16), pady=px(12))
        self._buttons: list[Button] = []
        for index, (label, variant) in enumerate(buttons):
            button = Button(row, fonts, label, lambda i=index: self._choose(i), variant=variant)
            button.pack(side="left", padx=(px(8), 0))
            self._buttons.append(button)

        self.protocol("WM_DELETE_WINDOW", self._dismiss)
        self.bind("<Escape>", lambda _event: self._dismiss())
        self.bind("<Return>", lambda _event: self._choose(default))
        self._place_over(parent.winfo_toplevel())
        self._buttons[default].focus_set()

    def _place_over(self, owner: tk.Misc) -> None:
        self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
        y = owner.winfo_rooty() + (owner.winfo_height() - height) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        try:
            winapi.use_dark_title_bar(int(self.wm_frame(), 16))
        except (ValueError, tk.TclError):
            pass
        self.deiconify()
        self.lift()
        self.grab_set()

    def _choose(self, index: int) -> None:
        self.result = index
        self.destroy()

    def _dismiss(self) -> None:
        self._choose(self._cancel if self._cancel is not None else self._default)

    @classmethod
    def show(cls, parent: tk.Misc, fonts: Fonts, title: str, message: str, **options: object):
        dialog = cls(parent, fonts, title, message, **options)
        dialog.wait_window()
        return dialog.result


def confirm(
    parent: tk.Misc,
    fonts: Fonts,
    title: str,
    message: str,
    *,
    confirm_label: str,
    cancel_label: str = "Cancelar",
    kind: str = "question",
    default_cancel: bool = False,
) -> bool:
    choice = MessageDialog.show(
        parent,
        fonts,
        title,
        message,
        kind=kind,
        buttons=((cancel_label, "secondary"), (confirm_label, "primary")),
        default=0 if default_cancel else 1,
        cancel=0,
    )
    return choice == 1


def inform(
    parent: tk.Misc,
    fonts: Fonts,
    title: str,
    message: str,
    *,
    kind: str = "info",
    detail: str | None = None,
) -> None:
    MessageDialog.show(
        parent, fonts, title, message, kind=kind, buttons=(("OK", "primary"),), detail=detail
    )
