"""Componentes visuais do tema escuro: botões, cartões, barras, listas e rolagem."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from tkinter import ttk

from maintenance.gui import theme
from maintenance.gui.model import Presentation
from maintenance.gui.theme import Fonts, px


def _is_inside(widget: tk.Misc, event: tk.Event) -> bool:
    """O ponteiro está sobre ``widget`` ou sobre um de seus filhos?"""
    target = widget.winfo_containing(event.x_root, event.y_root)
    while target is not None:
        if target is widget:
            return True
        target = target.master
    return False


class Button(tk.Frame):
    """Botão plano com ícone opcional, foco por teclado, destaque e estado desabilitado."""

    _VARIANTS: dict[str, tuple[str | None, str, str]] = {
        "primary": (theme.GREEN_BUTTON, theme.GREEN_BUTTON_HOVER, "#ffffff"),
        "secondary": (theme.SURFACE_HOVER, theme.SURFACE_ACTIVE, theme.TEXT),
        "ghost": (None, theme.SURFACE_HOVER, theme.TEXT_MUTED),
    }

    def __init__(
        self,
        parent: tk.Misc,
        fonts: Fonts,
        text: str,
        command: Callable[[], object] | None = None,
        *,
        icon: str | None = None,
        variant: str = "secondary",
        small: bool = False,
    ) -> None:
        parent_bg = parent.cget("bg")
        normal, hover, foreground = self._VARIANTS[variant]
        self._normal = normal or parent_bg
        self._hover = hover
        self._foreground = foreground
        self._command = command
        self._enabled = True
        super().__init__(
            parent,
            bg=self._normal,
            cursor="hand2",
            takefocus=1,
            highlightthickness=px(1),
            highlightbackground=parent_bg,
            highlightcolor=theme.FOCUS,
        )
        padx, pady = (px(10), px(4)) if small else (px(14), px(7))
        self._labels: list[tk.Label] = []
        glyph = fonts.glyph(icon) if icon else ""
        if glyph:
            icon_label = tk.Label(
                self,
                text=glyph,
                font=fonts.icon_small if small else fonts.icon,
                bg=self._normal,
                fg=foreground,
            )
            icon_label.pack(side="left", padx=(padx, 0), pady=pady)
            self._labels.append(icon_label)
        self._text = tk.Label(
            self,
            text=text,
            font=fonts.small if small else fonts.strong,
            bg=self._normal,
            fg=foreground,
        )
        self._text.pack(side="left", padx=(px(8) if glyph else padx, padx), pady=pady)
        self._labels.append(self._text)
        for widget in (self, *self._labels):
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)

    def _paint(self, background: str, foreground: str) -> None:
        self.configure(bg=background)
        for label in self._labels:
            label.configure(bg=background, fg=foreground)

    def _on_enter(self, _event: tk.Event) -> None:
        if self._enabled:
            self._paint(self._hover, self._foreground)

    def _on_leave(self, event: tk.Event) -> None:
        if self._enabled and not _is_inside(self, event):
            self._paint(self._normal, self._foreground)

    def _on_release(self, event: tk.Event) -> None:
        if self._enabled and _is_inside(self, event):
            self.invoke()

    def _on_key(self, _event: tk.Event) -> str:
        self.invoke()
        return "break"

    def invoke(self) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def set_text(self, text: str) -> None:
        self._text.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        if enabled:
            self._paint(self._normal, self._foreground)
        else:
            disabled_bg = (
                theme.SURFACE_HOVER if self._normal != self.master.cget("bg") else self._normal
            )
            self._paint(disabled_bg, theme.TEXT_FAINT)

    @property
    def enabled(self) -> bool:
        return self._enabled


class Card(tk.Frame):
    """Cartão grafite com borda fina; o conteúdo vai em ``card.body``."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        background: str = theme.SURFACE,
        border: str = theme.BORDER,
        padding: int = 16,
    ) -> None:
        super().__init__(
            parent,
            bg=background,
            highlightthickness=1,
            highlightbackground=border,
            highlightcolor=border,
        )
        self.body = tk.Frame(self, bg=background)
        self.body.pack(fill="both", expand=True, padx=px(padding), pady=px(padding))


class WrapLabel(tk.Label):
    """Rótulo que quebra linhas conforme a largura disponível (janela redimensionável)."""

    def __init__(self, parent: tk.Misc, **options: object) -> None:
        options.setdefault("justify", "left")
        options.setdefault("anchor", "w")
        options.setdefault("wraplength", px(360))
        options.setdefault("bg", parent.cget("bg"))
        super().__init__(parent, **options)
        self.bind("<Configure>", self._rewrap, add="+")

    def _rewrap(self, event: tk.Event) -> None:
        width = max(event.width - px(4), px(80))
        if abs(int(float(self.cget("wraplength"))) - width) > 2:
            self.configure(wraplength=width)


class Badge(tk.Label):
    """Selo de estado (OK, Atenção, Erro...) com fundo tingido."""

    def __init__(self, parent: tk.Misc, fonts: Fonts, text: str, level: str = "info") -> None:
        super().__init__(parent, font=fonts.small, padx=px(8), pady=0)
        self.set(text, level)

    def set(self, text: str, level: str) -> None:
        foreground, background = theme.LEVEL_COLORS[level]
        self.configure(text=text, fg=foreground, bg=background)


class LevelIcon(tk.Label):
    """Ícone colorido de estado (verde, amarelo, vermelho ou neutro)."""

    def __init__(self, parent: tk.Misc, fonts: Fonts, level: str = "info", *, size: str = "medium"):
        font = {"small": fonts.icon, "medium": fonts.icon_medium, "large": fonts.icon_large}[size]
        super().__init__(parent, font=font, bg=parent.cget("bg"))
        self._fonts = fonts
        self.set(level)

    def set(self, level: str) -> None:
        color, _background = theme.LEVEL_COLORS[level]
        self.configure(text=self._fonts.glyph(theme.LEVEL_GLYPHS[level]), fg=color)


class Spinner(tk.Canvas):
    """Indicador de atividade discreto (arco girando); só anima enquanto ativo."""

    def __init__(self, parent: tk.Misc, *, size: int = 18, color: str = theme.GREEN) -> None:
        side = px(size)
        super().__init__(
            parent, width=side, height=side, bg=parent.cget("bg"), highlightthickness=0, bd=0
        )
        pad = px(2) + 1
        line = max(px(2), 2)
        self.create_oval(pad, pad, side - pad, side - pad, outline=theme.SURFACE_ACTIVE, width=line)
        self._arc = self.create_arc(
            pad,
            pad,
            side - pad,
            side - pad,
            start=90,
            extent=100,
            style="arc",
            outline=color,
            width=line,
        )
        self._angle = 90
        self._job: str | None = None
        self.bind("<Destroy>", lambda _event: self.stop(), add="+")

    def start(self) -> None:
        if self._job is None:
            self._tick()

    def stop(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _tick(self) -> None:
        self._angle = (self._angle - 14) % 360
        self.itemconfigure(self._arc, start=self._angle)
        self._job = self.after(33, self._tick)


class ProgressBar(tk.Canvas):
    """Barra de progresso com transição suave entre valores."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        height: int = 6,
        color: str = theme.GREEN,
        track: str = theme.SURFACE_ACTIVE,
    ) -> None:
        super().__init__(parent, height=px(height), bg=track, highlightthickness=0, bd=0)
        self._fill = self.create_rectangle(0, 0, 0, px(height), fill=color, width=0)
        self._value = 0.0
        self._target = 0.0
        self._job: str | None = None
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<Destroy>", lambda _event: self._cancel(), add="+")

    def set(self, fraction: float, *, animate: bool = True) -> None:
        self._target = min(max(fraction, 0.0), 1.0)
        if not animate:
            self._cancel()
            self._value = self._target
            self._draw()
        elif self._job is None:
            self._animate()

    def set_color(self, color: str) -> None:
        self.itemconfigure(self._fill, fill=color)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def _animate(self) -> None:
        delta = self._target - self._value
        if abs(delta) < 0.003:
            self._value = self._target
            self._draw()
            self._job = None
            return
        self._value += delta * 0.2
        self._draw()
        self._job = self.after(16, self._animate)

    def _draw(self) -> None:
        self.coords(self._fill, 0, 0, self.winfo_width() * self._value, self.winfo_height())


class CheckOption(tk.Frame):
    """Opção selecionável: caixa de seleção, título, selos, descrição e observação."""

    def __init__(
        self,
        parent: tk.Misc,
        fonts: Fonts,
        variable: tk.BooleanVar,
        title: str,
        description: str = "",
        *,
        badges: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._background = parent.cget("bg")
        super().__init__(
            parent,
            bg=self._background,
            cursor="hand2",
            takefocus=1,
            highlightthickness=px(1),
            highlightbackground=self._background,
            highlightcolor=theme.FOCUS,
        )
        self._variable = variable
        self._enabled = True
        side = px(18)
        self._box = tk.Canvas(
            self, width=side, height=side, bg=self._background, highlightthickness=0, bd=0
        )
        self._box.grid(row=0, column=0, rowspan=3, sticky="n", padx=px(12), pady=(px(12), 0))
        header = tk.Frame(self, bg=self._background)
        header.grid(row=0, column=1, sticky="ew", padx=(0, px(12)), pady=(px(10), 0))
        self._title = tk.Label(
            header, text=title, font=fonts.strong, bg=self._background, fg=theme.TEXT
        )
        self._title.pack(side="left")
        self.badges: list[Badge] = []
        for text, level in badges:
            badge = Badge(header, fonts, text, level)
            badge.pack(side="left", padx=(px(8), 0))
            self.badges.append(badge)
        self._description = WrapLabel(self, text=description, font=fonts.small, fg=theme.TEXT_MUTED)
        self._description.grid(row=1, column=1, sticky="ew", padx=(0, px(12)), pady=(px(2), 0))
        self._note = WrapLabel(self, text="", font=fonts.small, fg=theme.YELLOW)
        self._spacer = tk.Frame(self, bg=self._background, height=px(10))
        self._spacer.grid(row=3, column=1)
        self.grid_columnconfigure(1, weight=1)
        self._paintable: list[tk.Misc] = [
            self,
            header,
            self._title,
            self._description,
            self._note,
            self._box,
            self._spacer,
        ]
        for widget in self._paintable:
            widget.bind("<ButtonRelease-1>", self._on_release, add="+")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        variable.trace_add("write", lambda *_args: self._draw_box())
        self._draw_box()

    def set_note(self, note: str) -> None:
        if note:
            self._note.configure(text=note)
            self._note.grid(row=2, column=1, sticky="ew", padx=(0, px(12)), pady=(px(4), 0))
        else:
            self._note.grid_remove()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        self._title.configure(fg=theme.TEXT if enabled else theme.TEXT_FAINT)
        self._draw_box()

    def toggle(self) -> None:
        if self._enabled:
            self._variable.set(not self._variable.get())

    def _paint(self, background: str) -> None:
        for widget in self._paintable:
            widget.configure(bg=background)

    def _on_enter(self, _event: tk.Event) -> None:
        if self._enabled:
            self._paint(theme.SURFACE_HOVER)

    def _on_leave(self, event: tk.Event) -> None:
        if not _is_inside(self, event):
            self._paint(self._background)

    def _on_release(self, event: tk.Event) -> None:
        if _is_inside(self, event):
            self.focus_set()
            self.toggle()

    def _on_key(self, _event: tk.Event) -> str:
        self.toggle()
        return "break"

    def _draw_box(self) -> None:
        canvas = self._box
        canvas.delete("all")
        side = int(canvas.cget("width"))
        if self._variable.get():
            color = theme.GREEN_BUTTON if self._enabled else theme.BORDER_STRONG
            canvas.create_rectangle(1, 1, side - 1, side - 1, fill=color, outline=color)
            canvas.create_line(
                side * 0.24,
                side * 0.52,
                side * 0.42,
                side * 0.70,
                side * 0.76,
                side * 0.31,
                fill="#ffffff",
                width=max(px(2), 2),
                capstyle="round",
                joinstyle="round",
            )
        else:
            outline = theme.TEXT_FAINT if self._enabled else theme.BORDER_STRONG
            canvas.create_rectangle(
                1, 1, side - 2, side - 2, outline=outline, width=max(px(1.5), 1)
            )


class NavItem(tk.Frame):
    """Item da barra lateral, com marcador de página ativa e indicador de atividade."""

    def __init__(
        self, parent: tk.Misc, fonts: Fonts, text: str, icon: str, command: Callable[[], None]
    ) -> None:
        background = theme.SIDEBAR
        super().__init__(
            parent,
            bg=background,
            cursor="hand2",
            takefocus=1,
            highlightthickness=px(1),
            highlightbackground=background,
            highlightcolor=theme.FOCUS,
        )
        self._command = command
        self._active = False
        self._busy_job: str | None = None
        self._pulse = False
        self._accent = tk.Frame(self, bg=background, width=px(3))
        self._accent.pack(side="left", fill="y")
        self._icon = tk.Label(
            self, text=fonts.glyph(icon), font=fonts.icon, bg=background, fg=theme.TEXT_MUTED
        )
        self._icon.pack(side="left", padx=(px(14), px(10)), pady=px(9))
        self._label = tk.Label(
            self, text=text, font=fonts.body, bg=background, fg=theme.TEXT_MUTED, anchor="w"
        )
        self._label.pack(side="left", fill="x", expand=True)
        dot = px(8)
        self._dot = tk.Canvas(
            self, width=dot, height=dot, bg=background, highlightthickness=0, bd=0
        )
        self._dot.pack(side="right", padx=(0, px(14)))
        self._dot_item = self._dot.create_oval(0, 0, dot - 1, dot - 1, fill=background, outline="")
        for widget in (self, self._icon, self._label, self._dot):
            widget.bind("<ButtonRelease-1>", self._on_release, add="+")
            widget.bind("<Enter>", lambda _event: self._paint(hover=True), add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Return>", lambda _event: self._command())
        self.bind("<space>", lambda _event: self._command())
        self.bind("<Destroy>", lambda _event: self._stop_pulse(), add="+")

    def _on_release(self, event: tk.Event) -> None:
        if _is_inside(self, event):
            self._command()

    def _on_leave(self, event: tk.Event) -> None:
        if not _is_inside(self, event):
            self._paint(hover=False)

    def _paint(self, *, hover: bool = False) -> None:
        if self._active:
            background, foreground = theme.SURFACE, theme.TEXT
        else:
            background = theme.SURFACE_HOVER if hover else theme.SIDEBAR
            foreground = theme.TEXT if hover else theme.TEXT_MUTED
        for widget in (self, self._icon, self._label, self._dot):
            widget.configure(bg=background)
        self._icon.configure(fg=theme.GREEN if self._active else foreground)
        self._label.configure(fg=foreground)
        self._accent.configure(bg=theme.GREEN if self._active else background)
        if self._busy_job is None:
            self._dot.itemconfigure(self._dot_item, fill=background)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint()

    def set_busy(self, busy: bool) -> None:
        if busy and self._busy_job is None:
            self._tick()
        elif not busy:
            self._stop_pulse()
            self._paint()

    def _stop_pulse(self) -> None:
        if self._busy_job is not None:
            try:
                self.after_cancel(self._busy_job)
            except tk.TclError:
                pass
            self._busy_job = None

    def _tick(self) -> None:
        self._pulse = not self._pulse
        self._dot.itemconfigure(
            self._dot_item, fill=theme.GREEN if self._pulse else theme.GREEN_BUTTON
        )
        self._busy_job = self.after(600, self._tick)


class ScrollFrame(tk.Frame):
    """Área com rolagem vertical; o conteúdo vai em ``scroll.body``.

    A roda do mouse é encaminhada pela janela principal (ver ``route_mousewheel``).
    """

    def __init__(self, parent: tk.Misc, *, background: str = theme.BG) -> None:
        super().__init__(parent, bg=background)
        self.canvas = tk.Canvas(
            self, bg=background, highlightthickness=0, bd=0, yscrollincrement=px(24)
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", style="Dark.Vertical.TScrollbar", command=self.canvas.yview
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body = tk.Frame(self.canvas, bg=background)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.body.bind("<Configure>", self._on_body_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self.canvas.scroll_owner = self  # type: ignore[attr-defined]

    def _on_body_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(
            scrollregion=(0, 0, self.body.winfo_reqwidth(), self.body.winfo_reqheight())
        )
        self._update_scrollbar()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._update_scrollbar()

    def _update_scrollbar(self) -> None:
        needed = self.body.winfo_reqheight() > self.canvas.winfo_height() + 1
        if needed and not self.scrollbar.winfo_ismapped():
            self.scrollbar.grid()
        elif not needed and self.scrollbar.winfo_ismapped():
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def scroll(self, units: int) -> None:
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)

    def scroll_to(self, widget: tk.Misc) -> None:
        """Rola até que ``widget`` (dentro de ``body``) fique visível no topo."""
        self.update_idletasks()
        height = max(self.body.winfo_reqheight(), 1)
        # A região de rolagem só seria atualizada no próximo <Configure>: atualiza agora
        # para que a posição calculada valha para o conteúdo atual.
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), height))
        self._update_scrollbar()
        offset = 0
        current: tk.Misc | None = widget
        while current is not None and current is not self.body:
            offset += current.winfo_y()
            current = current.master
        self.canvas.yview_moveto(max(offset - px(8), 0) / height)


def route_mousewheel(root: tk.Misc) -> None:
    """Rola a área sob o ponteiro (tabelas e campos de texto mantêm a própria rolagem)."""
    state = {"accumulated": 0}

    def on_wheel(event: tk.Event) -> None:
        widget = root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget.winfo_class() in ("Treeview", "Text"):
                return
            owner = getattr(widget, "scroll_owner", None)
            if owner is not None:
                state["accumulated"] += event.delta
                steps = int(state["accumulated"] / 60)
                state["accumulated"] -= steps * 60
                if steps:
                    owner.scroll(-steps)
                return
            widget = widget.master

    root.bind_all("<MouseWheel>", on_wheel, add="+")


class ResponsiveGrid(tk.Frame):
    """Grade de cartões que ajusta o número de colunas à largura da janela."""

    def __init__(
        self, parent: tk.Misc, *, min_width: int = 230, max_columns: int = 4, gap: int = 12
    ) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self._items: list[tk.Misc] = []
        self._min_width = px(min_width)
        self._max_columns = max_columns
        self._gap = px(gap)
        self._columns = 0
        self.bind("<Configure>", self._on_configure, add="+")

    def add(self, widget: tk.Misc) -> None:
        self._items.append(widget)
        self._layout()

    def _on_configure(self, event: tk.Event) -> None:
        columns = (event.width + self._gap) // (self._min_width + self._gap)
        columns = max(1, min(self._max_columns, columns))
        if columns != self._columns:
            self._columns = columns
            self._layout()

    def _layout(self) -> None:
        columns = self._columns or self._max_columns
        half = self._gap // 2
        for index, widget in enumerate(self._items):
            column = index % columns
            widget.grid(
                row=index // columns,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else half, 0 if column == columns - 1 else half),
                pady=(0, self._gap),
            )
        for column in range(self._max_columns):
            used = column < columns
            self.grid_columnconfigure(
                column, weight=1 if used else 0, uniform="cards" if used else ""
            )


class Banner(tk.Frame):
    """Faixa de destaque com ícone de estado, título, texto e ações."""

    def __init__(self, parent: tk.Misc, fonts: Fonts) -> None:
        super().__init__(parent, bg=theme.SURFACE, highlightthickness=1)
        self._fonts = fonts
        self._accent = tk.Frame(self, width=px(4), bg=theme.BORDER)
        self._accent.pack(side="left", fill="y")
        self._inner = tk.Frame(self, bg=theme.SURFACE)
        self._inner.pack(side="left", fill="both", expand=True, padx=px(16), pady=px(14))
        self._icon = LevelIcon(self._inner, fonts, size="large")
        self._icon.grid(row=0, column=0, rowspan=3, sticky="n", padx=(0, px(14)))
        self._title = tk.Label(self._inner, font=fonts.h2, anchor="w", bg=theme.SURFACE)
        self._title.grid(row=0, column=1, sticky="ew")
        self._text = WrapLabel(self._inner, font=fonts.body)
        self._text.grid(row=1, column=1, sticky="ew", pady=(px(2), 0))
        self.actions = tk.Frame(self._inner, bg=theme.SURFACE)
        self.actions.grid(row=2, column=1, sticky="w", pady=(px(10), 0))
        self._inner.grid_columnconfigure(1, weight=1)
        self.clear_actions()
        self.show("info", "")

    def show(self, level: str, title: str, text: str = "") -> None:
        color, background = theme.LEVEL_COLORS[level]
        for widget in (self, self._inner, self._title, self._text, self.actions, self._icon):
            widget.configure(bg=background)
        self.configure(highlightbackground=background, highlightcolor=background)
        self._accent.configure(bg=color)
        self._icon.set(level)
        self._title.configure(text=title, fg=theme.TEXT)
        self._text.configure(text=text, fg=theme.TEXT_MUTED if level == "info" else theme.TEXT)
        if text:
            self._text.grid()
        else:
            self._text.grid_remove()

    def clear_actions(self) -> None:
        for child in self.actions.winfo_children():
            child.destroy()
        self.actions.grid_remove()

    def add_action(
        self,
        text: str,
        command: Callable[[], object],
        *,
        icon: str | None = None,
        variant: str = "secondary",
    ) -> Button:
        self.actions.grid()
        button = Button(
            self.actions, self._fonts, text, command, icon=icon, variant=variant, small=True
        )
        button.pack(side="left", padx=(0, px(8)))
        return button


class ResultRow(tk.Frame):
    """Resultado de uma etapa: ícone de estado, título, selo, resumo e detalhes."""

    def __init__(self, parent: tk.Misc, fonts: Fonts, presentation: Presentation) -> None:
        background = parent.cget("bg")
        super().__init__(parent, bg=background)
        color, _tint = theme.LEVEL_COLORS[presentation.level]
        tk.Label(
            self,
            text=fonts.glyph(theme.LEVEL_GLYPHS[presentation.level]),
            font=fonts.icon_medium,
            fg=color,
            bg=background,
        ).grid(row=0, column=0, rowspan=4, sticky="n", padx=(0, px(12)), pady=(px(1), 0))
        header = tk.Frame(self, bg=background)
        header.grid(row=0, column=1, sticky="ew")
        tk.Label(
            header, text=presentation.title, font=fonts.strong, fg=theme.TEXT, bg=background
        ).pack(side="left")
        Badge(header, fonts, presentation.badge, presentation.level).pack(
            side="left", padx=(px(10), 0)
        )
        WrapLabel(self, text=presentation.text, font=fonts.body, fg=theme.TEXT_MUTED).grid(
            row=1, column=1, sticky="ew", pady=(px(3), 0)
        )
        if presentation.details:
            WrapLabel(
                self,
                text="\n".join(f"•  {line}" for line in presentation.details),
                font=fonts.small,
                fg=theme.TEXT_MUTED,
            ).grid(row=2, column=1, sticky="ew", pady=(px(6), 0))
        if presentation.errors:
            WrapLabel(
                self,
                text="\n".join(presentation.errors),
                font=fonts.small,
                fg=theme.RED,
            ).grid(row=3, column=1, sticky="ew", pady=(px(6), 0))
        self.grid_columnconfigure(1, weight=1)


class InfoTile(Card):
    """Cartão do painel: ícone, rótulo, valor principal, texto auxiliar e barra opcional."""

    def __init__(self, parent: tk.Misc, fonts: Fonts, icon: str, label: str) -> None:
        super().__init__(parent, padding=14)
        body = self.body
        header = tk.Frame(body, bg=theme.SURFACE)
        header.pack(fill="x")
        tk.Label(
            header, text=fonts.glyph(icon), font=fonts.icon, fg=theme.TEXT_FAINT, bg=theme.SURFACE
        ).pack(side="left", padx=(0, px(8)))
        tk.Label(header, text=label, font=fonts.small, fg=theme.TEXT_MUTED, bg=theme.SURFACE).pack(
            side="left"
        )
        self._value = WrapLabel(body, text="—", font=fonts.value, fg=theme.TEXT)
        self._value.pack(fill="x", pady=(px(8), 0))
        self._secondary = WrapLabel(body, text="", font=fonts.small, fg=theme.TEXT_MUTED)
        self._secondary.pack(fill="x", pady=(px(2), 0))
        self._bar = ProgressBar(body, height=4)

    def set(
        self,
        value: str,
        secondary: str = "",
        *,
        level: str | None = None,
        bar: float | None = None,
        bar_color: str = theme.GREEN,
    ) -> None:
        color = theme.LEVEL_COLORS[level][0] if level else theme.TEXT
        self._value.configure(text=value, fg=color)
        self._secondary.configure(text=secondary)
        if bar is None:
            self._bar.pack_forget()
        else:
            self._bar.pack(fill="x", pady=(px(10), 0))
            self._bar.set_color(bar_color)
            self._bar.set(bar)


def separator(parent: tk.Misc, *, color: str = theme.BORDER) -> tk.Frame:
    return tk.Frame(parent, bg=color, height=1)


def section_title(parent: tk.Misc, fonts: Fonts, text: str) -> tk.Label:
    return tk.Label(
        parent, text=text, font=fonts.h3, fg=theme.TEXT, bg=parent.cget("bg"), anchor="w"
    )
