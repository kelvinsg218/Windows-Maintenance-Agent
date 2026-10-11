"""Manutenção: executa o fluxo ``run`` apenas com as rotinas escolhidas e confirmadas."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maintenance.formatting import format_bytes, format_duration
from maintenance.gui import model, texts, theme, winapi
from maintenance.gui.dialogs import confirm, inform
from maintenance.gui.page_base import Page, ProgressPanel
from maintenance.gui.tasks import Emit
from maintenance.gui.theme import Glyph, px
from maintenance.gui.widgets import (
    Banner,
    Button,
    Card,
    CheckOption,
    ResultRow,
    WrapLabel,
    section_title,
    separator,
)
from maintenance.results import (
    TASK_CHROME,
    TASK_CLEANUP,
    TASK_WINDOWS_UPDATE,
    TASKS,
    Status,
)
from maintenance.runner import RunOptions, RunOutcome, run_maintenance

if TYPE_CHECKING:
    from maintenance.gui.app import MaintenanceApp


ROUTINE_TITLES: dict[str, str] = {
    TASK_CHROME: "Atualizar o Google Chrome",
    TASK_WINDOWS_UPDATE: "Instalar atualizações do Windows",
    TASK_CLEANUP: "Limpeza de arquivos temporários",
}
_ADMIN_BADGES: dict[str, str] = {
    TASK_CHROME: "Pode exigir administrador",
    TASK_WINDOWS_UPDATE: "Requer administrador",
    TASK_CLEANUP: "Parcial sem administrador",
}
_WITHOUT_ADMIN: dict[str, str] = {
    TASK_CHROME: "Sem administrador: o Chrome só é atualizado se estiver instalado apenas para o "
    "usuário atual.",
    TASK_WINDOWS_UPDATE: "Sem administrador: as atualizações são apenas pesquisadas e listadas, "
    "sem instalação.",
    TASK_CLEANUP: "Sem administrador: apenas a pasta Temp do usuário atual é limpa.",
}


def routine_description(task: str, min_age_days: int) -> str:
    if task == TASK_CHROME:
        return (
            "Verifica a versão instalada e aplica a atualização do canal estável pelo winget. "
            "Se o Chrome estiver aberto, a nova versão é aplicada quando ele for reiniciado."
        )
    if task == TASK_WINDOWS_UPDATE:
        return (
            "Pesquisa, baixa e instala atualizações de segurança e de qualidade pelo Windows "
            "Update. Drivers, atualizações opcionais e novas versões do Windows não são "
            "instalados. O computador nunca é reiniciado automaticamente."
        )
    return (
        f"Remove arquivos temporários com mais de {min_age_days} dias das pastas Temp. Arquivos "
        "recentes, em uso e documentos pessoais são preservados."
    )


class MaintenancePage(Page):
    name = texts.PAGE_MAINTENANCE

    def __init__(self, parent: tk.Misc, app: MaintenanceApp) -> None:
        super().__init__(parent, app)
        fonts = self.fonts
        admin = app.privileges.is_admin
        self._results_count = 0

        if not admin:
            notice = Banner(self.content, fonts)
            notice.show(
                "warning",
                "Sem privilégios de administrador",
                "Algumas rotinas serão executadas parcialmente ou ignoradas. Para executá-las por "
                "completo, reinicie o aplicativo como administrador (o Windows pedirá sua "
                "confirmação).",
            )
            notice.add_action(
                "Reiniciar como administrador", app.request_elevation, icon=Glyph.ADMIN
            )
            notice.pack(fill="x", pady=(0, px(16)))

        routines = Card(self.content)
        routines.pack(fill="x")
        section_title(routines.body, fonts, "Rotinas disponíveis").pack(fill="x")
        self._vars: dict[str, tk.BooleanVar] = {}
        self._routine_options: dict[str, CheckOption] = {}
        for task in TASKS:
            variable = tk.BooleanVar(self, value=False)
            variable.trace_add("write", lambda *_args: self._update_run_button())
            option = CheckOption(
                routines.body,
                fonts,
                variable,
                ROUTINE_TITLES[task],
                routine_description(task, app.config.cleanup_min_age_days),
                badges=[(_ADMIN_BADGES[task], "info" if admin else "warning")],
            )
            option.set_note("" if admin else _WITHOUT_ADMIN[task])
            option.pack(fill="x", pady=(px(4), 0))
            self._vars[task] = variable
            self._routine_options[task] = option

        separator(routines.body).pack(fill="x", pady=px(12))
        self._dry_run = tk.BooleanVar(self, value=False)
        self._dry_run.trace_add("write", lambda *_args: self._update_run_button())
        CheckOption(
            routines.body,
            fonts,
            self._dry_run,
            "Modo simulação",
            "Executa todo o processo sem alterar o computador e mostra o que seria feito. O "
            "relatório e o histórico ficam marcados como simulação.",
        ).pack(fill="x")

        actions = tk.Frame(routines.body, bg=theme.SURFACE)
        actions.pack(fill="x", pady=(px(14), 0))
        self._run_button = Button(
            actions, fonts, "Executar manutenção", self.start, icon=Glyph.PLAY, variant="primary"
        )
        self._run_button.pack(side="left")
        self._selection = tk.Label(actions, font=fonts.small, fg=theme.TEXT_FAINT, bg=theme.SURFACE)
        self._selection.pack(side="left", padx=px(12))
        WrapLabel(
            routines.body,
            text="Nenhuma rotina é executada automaticamente. Arquivos pessoais, o Registro, o "
            "Windows Defender e o firewall nunca são alterados.",
            font=fonts.small,
            fg=theme.TEXT_FAINT,
        ).pack(fill="x", pady=(px(12), 0))

        self._progress = ProgressPanel(self.content, fonts)
        self._summary = Banner(self.content, fonts)
        self._results = Card(self.content)
        self._update_run_button()

    # --- Seleção ------------------------------------------------------------------------

    def _selected(self) -> list[str]:
        return [task for task in TASKS if self._vars[task].get()]

    def _update_run_button(self) -> None:
        selected = self._selected()
        simulate = self._dry_run.get()
        self._run_button.set_text("Simular manutenção" if simulate else "Executar manutenção")
        self._run_button.set_enabled(bool(selected) and not self.app.busy)
        if not selected:
            hint = "Selecione ao menos uma rotina."
        else:
            hint = texts.plural(len(selected), "rotina selecionada", "rotinas selecionadas")
        self._selection.configure(text=hint)

    def preselect(self, tasks: list[str]) -> None:
        """Marca as rotinas sugeridas pela verificação (a execução ainda exige confirmação)."""
        for task in TASKS:
            self._vars[task].set(task in tasks)

    def on_show(self) -> None:
        self._update_run_button()

    def on_busy_changed(self) -> None:
        self._update_run_button()

    # --- Execução -----------------------------------------------------------------------

    def _confirmation_message(self, selected: list[str], dry_run: bool) -> str:
        lines = ["Rotinas selecionadas:"]
        lines += [f"•  {ROUTINE_TITLES[task]}" for task in selected]
        lines.append("")
        if dry_run:
            lines.append("Modo simulação: nenhuma alteração será feita no computador.")
        if not self.app.privileges.is_admin:
            lines.append(
                "Sem privilégios de administrador, parte das rotinas será ignorada (veja os "
                "avisos de cada rotina)."
            )
        lines.append(
            "O computador não será reiniciado automaticamente. Mantenha o aplicativo aberto até "
            "a conclusão."
        )
        return "\n".join(lines)

    def start(self) -> None:
        selected = self._selected()
        if not selected or not self.app.require_valid_config():
            return
        dry_run = self._dry_run.get()
        accepted = confirm(
            self.app.root,
            self.fonts,
            "Confirmar simulação" if dry_run else "Confirmar manutenção",
            self._confirmation_message(selected, dry_run),
            confirm_label="Simular" if dry_run else "Executar",
        )
        if not accepted:
            return
        app = self.app
        config, paths = app.config, app.paths
        services = app.services_factory()
        options = RunOptions(tasks=tuple(selected), dry_run=dry_run, console=False)

        def job(emit: Emit) -> RunOutcome:
            return run_maintenance(
                config,
                paths,
                options,
                services,
                on_result=lambda result: emit("result", result),
                progress=lambda message: emit("progress", message),
            )

        started = app.start_operation(
            self.name, job, on_event=self._on_event, on_done=self._on_done, on_error=self._on_error
        )
        if not started:
            return
        self._results_count = 0
        self._summary.pack_forget()
        self._results.pack_forget()
        for child in self._results.body.winfo_children():
            child.destroy()
        self._run_button.set_text("Em andamento...")
        self._progress.pack(fill="x", pady=(px(16), 0))
        self._progress.begin(
            "Simulação em andamento" if dry_run else "Manutenção em andamento",
            "Não feche o aplicativo até a conclusão. Você pode acompanhar o andamento aqui ou "
            "navegar pelas outras telas.",
        )
        self.after_idle(lambda: self.scroll.scroll_to(self._progress))

    def _on_event(self, kind: str, payload: Any) -> None:
        if kind == "progress":
            self._progress.set_step(str(payload))
        elif kind == "result":
            self._results_count += 1
            self._progress.set_fraction(self._results_count / (model.RUN_RESULT_COUNT + 1))

    def _on_done(self, outcome: RunOutcome) -> None:
        report = outcome.report
        level = texts.OVERALL_LEVELS[outcome.overall]
        title = texts.OVERALL_TITLES[outcome.overall]
        if report.dry_run:
            title = title.replace("Manutenção", "Simulação").replace("manutenção", "simulação")
        facts = [f"Duração: {format_duration(report.duration_seconds)}"]
        if outcome.run_id:
            facts.append(f"Registro nº {outcome.run_id} no histórico")
        lines = [" · ".join(facts)]
        if report.dry_run:
            lines.append("Simulação: nenhuma alteração foi feita no computador.")
        if report.reboot and report.reboot.required:
            lines.append(
                "Reinicialização necessária: reinicie o computador quando for conveniente "
                "(o aplicativo não reinicia automaticamente)."
            )
        if report.critical_error:
            lines.append(f"Erro crítico: {report.critical_error}")
        self._progress.finish()
        self._summary.show(level, title, "\n".join(lines))
        self._summary.clear_actions()
        if outcome.report_file:
            report_file = outcome.report_file
            self._summary.add_action(
                "Abrir relatório", lambda: self._open(report_file), icon=Glyph.DOCUMENT
            )
            self._summary.add_action(
                "Abrir pasta de relatórios",
                lambda: self._open(report_file.parent),
                icon=Glyph.FOLDER,
            )
        log_file = outcome.log_file
        self._summary.add_action("Abrir log técnico", lambda: self._open(log_file), icon=Glyph.LOG)
        self._summary.pack(fill="x", pady=(px(16), 0))
        self._show_results(outcome)
        self._update_run_button()
        self.app.pages[texts.PAGE_DASHBOARD].refresh()
        self.after_idle(lambda: self.scroll.scroll_to(self._summary))

    def _show_results(self, outcome: RunOutcome) -> None:
        report = outcome.report
        body = self._results.body
        section_title(body, self.fonts, "Resultado de cada etapa").pack(fill="x", pady=(0, px(4)))
        results = model.visible_results(report.modules, report.requested_tasks)
        if outcome.network_sync.status is not Status.SKIPPED:
            results.append(outcome.network_sync)
        for result in results:
            presentation = model.present_result(result, applied=True)
            if result.module == "disk":
                presentation = _with_disk_change(
                    presentation, report.disk_before, report.disk_after
                )
            separator(body).pack(fill="x", pady=px(12))
            ResultRow(body, self.fonts, presentation).pack(fill="x")
        self._results.pack(fill="x", pady=(px(16), 0))

    def _on_error(self, exc: BaseException, detail: str) -> None:
        self.app.log_error(detail)
        title, message = self.app.describe_error(exc)
        self._progress.finish()
        self._summary.clear_actions()
        self._summary.show("error", title, message)
        self._summary.pack(fill="x", pady=(px(16), 0))
        self._update_run_button()

    def _open(self, path: Path) -> None:
        if not winapi.open_path(path):
            inform(
                self.app.root,
                self.fonts,
                "Arquivo indisponível",
                f"Não foi possível abrir:\n{path}",
                kind="warning",
            )


def _with_disk_change(
    presentation: model.Presentation, before: Any, after: Any
) -> model.Presentation:
    if before is None or after is None:
        return presentation
    change = after.free_bytes - before.free_bytes
    detail = (
        f"Espaço livre antes: {format_bytes(before.free_bytes)} · depois: "
        f"{format_bytes(after.free_bytes)}"
    )
    if change > 0:
        detail += f" (+{format_bytes(change)})"
    return model.Presentation(
        presentation.level,
        presentation.badge,
        presentation.title,
        presentation.text,
        [detail, *presentation.details],
        presentation.errors,
    )
