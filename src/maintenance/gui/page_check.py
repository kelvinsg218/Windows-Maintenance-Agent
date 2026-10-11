"""Verificação do sistema: executa o fluxo ``check`` (somente leitura) com progresso."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Any

from maintenance.formatting import format_duration
from maintenance.gui import model, texts, theme, winapi
from maintenance.gui.dialogs import inform
from maintenance.gui.page_base import Page, ProgressPanel
from maintenance.gui.tasks import Emit
from maintenance.gui.theme import Glyph, px
from maintenance.gui.widgets import (
    Badge,
    Banner,
    Button,
    Card,
    CheckOption,
    ResultRow,
    WrapLabel,
    section_title,
    separator,
)
from maintenance.results import TASK_CHROME, TASK_CLEANUP, TASK_WINDOWS_UPDATE, ModuleResult
from maintenance.runner import CheckOutcome, run_check, select_tasks

if TYPE_CHECKING:
    from maintenance.gui.app import MaintenanceApp

_OPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        TASK_CHROME,
        "Google Chrome",
        "Procura uma versão mais recente do navegador pelo winget.",
    ),
    (
        TASK_WINDOWS_UPDATE,
        "Windows Update",
        "Pesquisa atualizações pendentes do Windows (equivale a “Verificar atualizações” nas "
        "Configurações). Pode levar alguns minutos.",
    ),
    (
        TASK_CLEANUP,
        "Arquivos temporários",
        "Estima quanto espaço pode ser liberado pela limpeza, sem apagar nada.",
    ),
)


class CheckPage(Page):
    name = texts.PAGE_CHECK

    def __init__(self, parent: tk.Misc, app: MaintenanceApp) -> None:
        super().__init__(parent, app)
        fonts = self.fonts
        self._steps: list[str] = []
        self._done = 0
        self._outcome: CheckOutcome | None = None

        options = Card(self.content)
        options.pack(fill="x")
        section_title(options.body, fonts, "O que verificar").pack(fill="x")
        self._vars: dict[str, tk.BooleanVar] = {}
        enabled = {
            TASK_CHROME: app.config.update_chrome,
            TASK_WINDOWS_UPDATE: app.config.update_windows,
            TASK_CLEANUP: app.config.cleanup_temp,
        }
        for task, title, description in _OPTIONS:
            variable = tk.BooleanVar(self, value=enabled[task])
            variable.trace_add("write", lambda *_args: self._update_start_button())
            self._vars[task] = variable
            CheckOption(options.body, fonts, variable, title, description).pack(
                fill="x", pady=(px(4), 0)
            )
        WrapLabel(
            options.body,
            text="Privilégios, espaço em disco e reinicialização pendente são sempre verificados. "
            "A verificação não altera nada no computador.",
            font=fonts.small,
            fg=theme.TEXT_FAINT,
        ).pack(fill="x", pady=(px(12), px(14)))
        actions = tk.Frame(options.body, bg=theme.SURFACE)
        actions.pack(fill="x")
        self._start_button = Button(
            actions, fonts, "Iniciar verificação", self.start, icon=Glyph.PLAY, variant="primary"
        )
        self._start_button.pack(side="left")
        self._selection_hint = tk.Label(
            actions, font=fonts.small, fg=theme.TEXT_FAINT, bg=theme.SURFACE
        )
        self._selection_hint.pack(side="left", padx=px(12))

        self._progress = ProgressPanel(self.content, fonts)
        self._summary = Banner(self.content, fonts)
        self._recommendations = Card(self.content)
        self._results = Card(self.content)
        self._update_start_button()

    # --- Início -------------------------------------------------------------------------

    def _selected(self) -> list[str]:
        return [task for task, variable in self._vars.items() if variable.get()]

    def _update_start_button(self) -> None:
        has_selection = bool(self._selected())
        self._start_button.set_enabled(has_selection and not self.app.busy)
        self._selection_hint.configure(
            text="" if has_selection else "Selecione ao menos um item para verificar."
        )

    def start(self) -> None:
        selected = self._selected()
        if not selected or not self.app.require_valid_config():
            return
        app = self.app
        config, paths = app.config, app.paths
        services = app.services_factory()
        self._steps = model.check_steps(select_tasks(config, selected))
        self._done = 0

        def job(emit: Emit) -> CheckOutcome:
            return run_check(
                config,
                paths,
                tasks=tuple(selected),
                services=services,
                on_result=lambda result: emit("result", result),
                progress=lambda message: emit("progress", message),
            )

        started = app.start_operation(
            self.name, job, on_event=self._on_event, on_done=self._on_done, on_error=self._on_error
        )
        if not started:
            return
        self._reset_results()
        self._start_button.set_enabled(False)
        self._start_button.set_text("Verificando...")
        self._progress.pack(fill="x", pady=(px(16), 0))
        self._progress.begin(
            "Verificação em andamento",
            "Você pode navegar pelas outras telas enquanto a verificação é feita.",
        )
        self._progress.set_step(model.CHECK_STEP_MESSAGES[self._steps[0]])

    def _reset_results(self) -> None:
        self._outcome = None
        for card in (self._summary, self._recommendations, self._results):
            card.pack_forget()
        for card in (self._recommendations, self._results):
            for child in card.body.winfo_children():
                child.destroy()
        section_title(self._results.body, self.fonts, "Resultados da verificação").pack(
            fill="x", pady=(0, px(4))
        )

    # --- Andamento ----------------------------------------------------------------------

    def _on_event(self, kind: str, payload: Any) -> None:
        if kind == "progress":
            self._progress.set_step(str(payload))
        elif kind == "result":
            self._add_result(payload)

    def _add_result(self, result: ModuleResult) -> None:
        self._done += 1
        self._progress.set_fraction(self._done / max(len(self._steps), 1))
        if self._done < len(self._steps):
            self._progress.set_step(model.CHECK_STEP_MESSAGES[self._steps[self._done]])
        if not self._results.winfo_ismapped():
            self._results.pack(fill="x", pady=(px(16), 0))
        body = self._results.body
        separator(body).pack(fill="x", pady=px(12))
        ResultRow(body, self.fonts, model.present_result(result)).pack(fill="x")

    def _on_done(self, outcome: CheckOutcome) -> None:
        self._outcome = outcome
        self._finish_buttons()
        summary = model.summarize_check(outcome)
        duration = format_duration(self._progress.finish())
        self._summary.show(summary.level, summary.title, f"{summary.text} · Duração: {duration}")
        self._summary.clear_actions()
        recommendations = [
            recommendation
            for result in outcome.results
            if (recommendation := model.recommend(result, is_admin=outcome.privileges.is_admin))
        ]
        fixable = list(dict.fromkeys(r.task for r in recommendations if r.task))
        if fixable:
            self._summary.add_action(
                "Corrigir na Manutenção",
                lambda: self._open_maintenance(fixable),
                icon=Glyph.WRENCH,
                variant="primary",
            )
        if outcome.log_file:
            log_file = outcome.log_file
            self._summary.add_action(
                "Abrir log técnico", lambda: self._open(log_file), icon=Glyph.LOG
            )
        self._pack_before_results(self._summary)
        if recommendations:
            self._show_recommendations(recommendations)
            self._pack_before_results(self._recommendations)
        self.after_idle(lambda: self.scroll.scroll_to(self._summary))

    def _on_error(self, exc: BaseException, detail: str) -> None:
        self._finish_buttons()
        self.app.log_error(detail)
        title, message = self.app.describe_error(exc)
        self._progress.finish()
        self._summary.clear_actions()
        self._summary.show("error", title, message)
        self._pack_before_results(self._summary)

    def _pack_before_results(self, widget: tk.Misc) -> None:
        options: dict[str, Any] = {"fill": "x", "pady": (px(16), 0)}
        if self._results.winfo_manager() == "pack":
            options["before"] = self._results
        widget.pack(**options)

    def _finish_buttons(self) -> None:
        self._start_button.set_text("Verificar novamente")
        self._update_start_button()

    def on_busy_changed(self) -> None:
        self._update_start_button()

    def _show_recommendations(self, recommendations: list[model.Recommendation]) -> None:
        body = self._recommendations.body
        fonts = self.fonts
        section_title(body, fonts, "Recomendações").pack(fill="x")
        for recommendation in recommendations:
            row = tk.Frame(body, bg=theme.SURFACE)
            row.pack(fill="x", pady=(px(12), 0))
            color, _tint = theme.LEVEL_COLORS[recommendation.level]
            tk.Label(
                row,
                text=fonts.glyph(
                    Glyph.LIGHTBULB
                    if recommendation.level == "info"
                    else theme.LEVEL_GLYPHS[recommendation.level]
                ),
                font=fonts.icon,
                fg=color,
                bg=theme.SURFACE,
            ).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, px(12)))
            WrapLabel(row, text=recommendation.text, font=fonts.body, fg=theme.TEXT).grid(
                row=0, column=1, sticky="ew"
            )
            badges: list[tuple[str, str]] = []
            if recommendation.task:
                badges.append(("Corrigível na Manutenção", "ok"))
            elif recommendation.level != "info":
                badges.append(("Requer ação manual", "info"))
            if recommendation.needs_admin:
                badges.append(("Requer administrador", "warning"))
            if badges:
                tags = tk.Frame(row, bg=theme.SURFACE)
                tags.grid(row=1, column=1, sticky="w", pady=(px(4), 0))
                for index, (text, level) in enumerate(badges):
                    Badge(tags, fonts, text, level).pack(
                        side="left", padx=(px(6) if index else 0, 0)
                    )
            row.grid_columnconfigure(1, weight=1)

    # --- Ações --------------------------------------------------------------------------

    def _open_maintenance(self, tasks: list[str]) -> None:
        maintenance = self.app.pages[texts.PAGE_MAINTENANCE]
        maintenance.preselect(tasks)  # type: ignore[attr-defined]
        self.app.show_page(texts.PAGE_MAINTENANCE)

    def _open(self, path: Any) -> None:
        if not winapi.open_path(path):
            inform(
                self.app.root,
                self.fonts,
                "Arquivo indisponível",
                f"Não foi possível abrir o arquivo:\n{path}",
                kind="warning",
            )

    def on_show(self) -> None:
        self._update_start_button()
