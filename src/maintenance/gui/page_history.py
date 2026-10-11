"""Histórico: execuções registradas pelo ``run`` (banco SQLite local, somente leitura)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from maintenance.formatting import format_bytes, format_datetime, format_duration, yes_no
from maintenance.gui import model, texts, theme, winapi
from maintenance.gui.dialogs import inform
from maintenance.gui.page_base import Page
from maintenance.gui.tasks import Emit
from maintenance.gui.theme import Glyph, px
from maintenance.gui.widgets import (
    Badge,
    Banner,
    Button,
    Card,
    ResultRow,
    Spinner,
    WrapLabel,
    separator,
)
from maintenance.history import HistoryEntry, open_history_readonly
from maintenance.results import ModuleResult, Status

if TYPE_CHECKING:
    from maintenance.gui.app import MaintenanceApp

HISTORY_LIMIT = 100
_COLUMNS: tuple[tuple[str, str, int, bool], ...] = (
    ("id", "Nº", 56, False),
    ("date", "Data", 140, False),
    ("duration", "Duração", 90, False),
    ("result", "Resultado", 190, False),
    ("tasks", "Rotinas", 260, True),
    ("reboot", "Reinicialização", 120, False),
    ("mode", "Modo", 96, False),
)


class HistoryPage(Page):
    name = texts.PAGE_HISTORY

    def __init__(self, parent: tk.Misc, app: MaintenanceApp) -> None:
        super().__init__(parent, app)
        fonts = self.fonts
        self._entries: dict[str, HistoryEntry] = {}
        self._loading = False

        self._refresh_button = Button(
            self.header_actions, fonts, "Atualizar", self.refresh, icon=Glyph.REFRESH
        )
        self._refresh_button.pack(side="right")
        self._spinner = Spinner(self.header_actions)

        self._message = Banner(self.content, fonts)

        self._table = Card(self.content, padding=1)
        frame = self._table.body
        self._tree = ttk.Treeview(
            frame,
            columns=[column for column, *_rest in _COLUMNS],
            show="headings",
            style="History.Treeview",
            selectmode="browse",
            height=8,
        )
        for column, heading, width, stretch in _COLUMNS:
            self._tree.heading(column, text=heading, anchor="w")
            self._tree.column(column, width=px(width), minwidth=px(48), stretch=stretch, anchor="w")
        for level in ("warning", "error"):
            self._tree.tag_configure(level, foreground=theme.LEVEL_COLORS[level][0])
        self._tree.tag_configure("info", foreground=theme.TEXT_MUTED)
        scrollbar = ttk.Scrollbar(
            frame, orient="vertical", style="Dark.Vertical.TScrollbar", command=self._tree.yview
        )
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        frame.grid_columnconfigure(0, weight=1)
        self._tree.bind("<<TreeviewSelect>>", lambda _event: self._on_select())
        self._limit_note = WrapLabel(self.content, font=fonts.small, fg=theme.TEXT_FAINT)

        self._detail = Card(self.content)

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refresh_button.set_enabled(False)
        self._spinner.pack(side="right", padx=(0, px(10)), pady=px(6))
        self._spinner.start()
        history_db = self.app.paths.history_db

        def job(_emit: Emit) -> list[HistoryEntry]:
            store = open_history_readonly(history_db)
            return store.list_runs(HISTORY_LIMIT) if store else []

        self.app.run_background("history", job, on_done=self._populate, on_error=self._failed)

    def _finish_loading(self) -> None:
        self._loading = False
        self._spinner.stop()
        self._spinner.pack_forget()
        self._refresh_button.set_enabled(True)

    def _failed(self, exc: BaseException, detail: str) -> None:
        self._finish_loading()
        self.app.log_error(detail)
        self._table.pack_forget()
        self._detail.pack_forget()
        self._limit_note.pack_forget()
        self._message.clear_actions()
        self._message.show(
            "error",
            "Não foi possível ler o histórico",
            f"{type(exc).__name__}: {exc}",
        )
        self._message.pack(fill="x")

    def _populate(self, entries: list[HistoryEntry]) -> None:
        self._finish_loading()
        previous = self._tree.selection()
        self._tree.delete(*self._tree.get_children())
        self._entries = {}
        if not entries:
            self._table.pack_forget()
            self._detail.pack_forget()
            self._limit_note.pack_forget()
            self._message.show(
                "info",
                "Nenhuma manutenção registrada",
                "As manutenções executadas neste computador (inclusive as simulações) aparecem "
                "aqui, com o resultado de cada etapa.",
            )
            self._message.clear_actions()
            self._message.add_action(
                "Ir para Manutenção",
                lambda: self.app.show_page(texts.PAGE_MAINTENANCE),
                icon=Glyph.WRENCH,
            )
            self._message.pack(fill="x")
            return
        self._message.pack_forget()
        for entry in entries:
            label, level = texts.history_result(entry.result)
            item = str(entry.id)
            self._entries[item] = entry
            self._tree.insert(
                "",
                "end",
                iid=item,
                values=(
                    f"#{entry.id}",
                    format_datetime(entry.started_at),
                    format_duration(entry.duration_seconds),
                    label,
                    texts.task_list(entry.tasks),
                    "Necessária" if entry.reboot_required else "—",
                    "Simulação" if entry.dry_run else "Normal",
                ),
                tags=(level,),
            )
        self._table.pack(fill="x")
        if len(entries) >= HISTORY_LIMIT:
            self._limit_note.configure(text=f"Exibindo as últimas {HISTORY_LIMIT} execuções.")
            self._limit_note.pack(fill="x", pady=(px(6), 0), after=self._table)
        else:
            self._limit_note.pack_forget()
        selected = previous[0] if previous and previous[0] in self._entries else str(entries[0].id)
        self._tree.selection_set(selected)
        self._tree.see(selected)

    def _on_select(self) -> None:
        selection = self._tree.selection()
        entry = self._entries.get(selection[0]) if selection else None
        if entry is not None:
            self._show_detail(entry)

    def _show_detail(self, entry: HistoryEntry) -> None:
        fonts = self.fonts
        body = self._detail.body
        for child in body.winfo_children():
            child.destroy()
        header = tk.Frame(body, bg=theme.SURFACE)
        header.pack(fill="x")
        tk.Label(
            header, text=f"Manutenção nº {entry.id}", font=fonts.h3, fg=theme.TEXT, bg=theme.SURFACE
        ).pack(side="left")
        label, level = texts.history_result(entry.result)
        Badge(header, fonts, label, level).pack(side="left", padx=(px(10), 0))
        if entry.dry_run:
            Badge(header, fonts, "Simulação", "info").pack(side="left", padx=(px(6), 0))
        facts = [f"Início: {format_datetime(entry.started_at, seconds=True)}"]
        if entry.finished_at:
            facts.append(f"Duração: {format_duration(entry.duration_seconds)}")
        facts.append(f"Rotinas: {texts.task_list(entry.tasks)}")
        facts.append(f"Versão do agente: {entry.agent_version}")
        WrapLabel(body, text=" · ".join(facts), font=fonts.small, fg=theme.TEXT_MUTED).pack(
            fill="x", pady=(px(4), 0)
        )

        report = model.load_report(entry)
        if report is not None:
            requested = report.get("requested_tasks") or entry.tasks.split(",")
            results = model.visible_results(model.report_modules(report), requested)
            if report.get("critical_error"):
                summary = f"A manutenção foi interrompida: {report['critical_error']}"
                results.insert(0, ModuleResult("critical_error", Status.FAILED, summary))
            for result in results:
                separator(body).pack(fill="x", pady=px(12))
                presentation = model.present_result(result, applied=True)
                ResultRow(body, fonts, presentation).pack(fill="x")
        else:
            separator(body).pack(fill="x", pady=px(12))
            WrapLabel(
                body,
                text="O relatório detalhado desta execução não está disponível (a execução foi "
                "interrompida ou o arquivo foi removido pela retenção de relatórios). Resumo "
                "registrado no histórico:",
                font=fonts.body,
                fg=theme.TEXT_MUTED,
            ).pack(fill="x")
            WrapLabel(
                body,
                text="\n".join(f"•  {line}" for line in _summary_lines(entry)),
                font=fonts.body,
                fg=theme.TEXT,
            ).pack(fill="x", pady=(px(6), 0))

        actions = tk.Frame(body, bg=theme.SURFACE)
        actions.pack(fill="x", pady=(px(16), 0))
        for text, path, icon in (
            ("Abrir relatório", entry.report_file, Glyph.DOCUMENT),
            ("Abrir log técnico", entry.log_file, Glyph.LOG),
        ):
            if path and Path(path).is_file():
                Button(
                    actions, fonts, text, lambda p=Path(path): self._open(p), icon=icon, small=True
                ).pack(side="left", padx=(0, px(8)))
        self._detail.pack(fill="x", pady=(px(16), 0))

    def _open(self, path: Path) -> None:
        if not winapi.open_path(path):
            inform(
                self.app.root,
                self.fonts,
                "Arquivo indisponível",
                f"Não foi possível abrir:\n{path}",
                kind="warning",
            )


def _summary_lines(entry: HistoryEntry) -> list[str]:
    chrome = "—" if entry.chrome_updated is None else yes_no(entry.chrome_updated)
    cleanup = (
        f"Sim ({format_bytes(entry.bytes_removed)} removidos)" if entry.cleanup_performed else "Não"
    )
    lines = [
        f"Google Chrome atualizado: {chrome}",
        f"Atualizações do Windows instaladas: {entry.windows_updates_installed}",
        f"Limpeza realizada: {cleanup}",
        f"Reinicialização necessária: {yes_no(entry.reboot_required)}",
    ]
    if entry.network_sync:
        sync = _NETWORK_SYNC_LABELS.get(entry.network_sync, entry.network_sync)
        lines.append(f"Sincronização com a rede: {sync}")
    return lines


_NETWORK_SYNC_LABELS = {
    Status.SUCCESS.value: "concluída",
    Status.WARNING.value: "falhou (arquivos mantidos no computador)",
    Status.FAILED.value: "falhou",
}
