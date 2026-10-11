"""Painel: informações do computador e status geral (mesmas consultas do comando ``status``)."""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING

from maintenance.chrome import SCOPE_MACHINE
from maintenance.formatting import format_bytes, format_datetime, format_uptime
from maintenance.gui import model, texts, theme
from maintenance.gui.page_base import Page
from maintenance.gui.tasks import Emit
from maintenance.gui.theme import Glyph, px
from maintenance.gui.widgets import Banner, Button, InfoTile, ResponsiveGrid, Spinner, section_title
from maintenance.results import Status

if TYPE_CHECKING:
    from maintenance.gui.app import MaintenanceApp

_OK_TEXT = (
    "As verificações rápidas não encontraram pendências. Para procurar atualizações e "
    "arquivos temporários, use a Verificação do sistema."
)


class DashboardPage(Page):
    name = texts.PAGE_DASHBOARD

    def __init__(self, parent: tk.Misc, app: MaintenanceApp) -> None:
        super().__init__(parent, app)
        fonts = self.fonts
        self._loading = False
        self._loaded = False
        self.data: model.DashboardData | None = None

        self._refresh_button = Button(
            self.header_actions, fonts, "Atualizar", self.refresh, icon=Glyph.REFRESH
        )
        self._refresh_button.pack(side="right")
        self._spinner = Spinner(self.header_actions)

        section_title(self.content, fonts, "Status geral do sistema").pack(fill="x")
        self.status = Banner(self.content, fonts)
        self.status.pack(fill="x", pady=(px(10), px(26)))
        self.status.show("info", "Coletando informações...", "Aguarde alguns instantes.")

        section_title(self.content, fonts, "Informações do sistema").pack(fill="x")
        system = ResponsiveGrid(self.content)
        system.pack(fill="x", pady=(px(10), px(14)))
        self.tiles: dict[str, InfoTile] = {}
        for key, icon, label in (
            ("hostname", Glyph.PC, "Nome do computador"),
            ("windows", Glyph.WINDOWS, "Sistema operacional"),
            ("processor", Glyph.CPU, "Processador"),
            ("memory", Glyph.MEMORY, "Memória RAM"),
            ("disk", Glyph.DISK, "Armazenamento"),
            ("uptime", Glyph.CLOCK, "Tempo de atividade"),
        ):
            self.tiles[key] = InfoTile(system, fonts, icon, label)
            system.add(self.tiles[key])

        section_title(self.content, fonts, "Manutenção").pack(fill="x")
        maintenance = ResponsiveGrid(self.content)
        maintenance.pack(fill="x", pady=(px(10), 0))
        for key, icon, label in (
            ("privileges", Glyph.SHIELD, "Privilégios"),
            ("reboot", Glyph.RESTART, "Reinicialização pendente"),
            ("last_run", Glyph.HISTORY, "Última manutenção"),
            ("chrome", Glyph.GLOBE, "Google Chrome"),
            ("winget", Glyph.PACKAGE, "Gerenciador de pacotes (winget)"),
        ):
            self.tiles[key] = InfoTile(maintenance, fonts, icon, label)
            maintenance.add(self.tiles[key])

    def on_show(self) -> None:
        if not self._loaded and not self._loading:
            self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._refresh_button.set_enabled(False)
        self._spinner.pack(side="right", padx=(0, px(10)), pady=px(6))
        self._spinner.start()
        app = self.app
        config, paths, factory = app.config, app.paths, app.services_factory
        memory_status, cpu_count = app.memory_status, app.cpu_count

        def job(_emit: Emit) -> model.DashboardData:
            return model.collect_dashboard(
                factory(), config, paths, memory_status=memory_status, cpu_count=cpu_count
            )

        app.run_background("dashboard", job, on_done=self._on_loaded, on_error=self._on_failed)

    def _finish_loading(self) -> None:
        self._loading = False
        self._spinner.stop()
        self._spinner.pack_forget()
        self._refresh_button.set_enabled(True)

    def _on_loaded(self, data: model.DashboardData) -> None:
        self._finish_loading()
        self._loaded = True
        self.data = data
        self._show_status(data)
        self._show_system(data)
        self._show_maintenance(data)

    def _on_failed(self, exc: BaseException, detail: str) -> None:
        self._finish_loading()
        self.app.log_error(detail)
        title, message = self.app.describe_error(exc)
        self.status.clear_actions()
        self.status.show("error", "Não foi possível coletar as informações", f"{title}: {message}")

    def _show_status(self, data: model.DashboardData | None) -> None:
        assessment = model.assess_dashboard(data, config_error=self.app.config_error)
        if assessment.items:
            text = "\n".join(f"•  {item}" for _level, item in assessment.items)
        else:
            text = _OK_TEXT
        self.status.show(assessment.level, assessment.title, text)
        self.status.clear_actions()
        self.status.add_action(
            "Verificar o sistema",
            lambda: self.app.show_page(texts.PAGE_CHECK),
            icon=Glyph.DIAGNOSTIC,
        )

    def _show_system(self, data: model.DashboardData) -> None:
        info, tiles = data.info, self.tiles
        tiles["hostname"].set(
            info.hostname, f"Usuário: {info.username} · Arquitetura {info.architecture}"
        )
        windows = info.windows
        version = " · ".join(
            part
            for part in (
                f"Versão {windows.display_version}" if windows.display_version else "",
                f"build {windows.build}" if windows.build else "",
            )
            if part
        )
        tiles["windows"].set(windows.product_name, version)
        threads = data.logical_processors
        tiles["processor"].set(
            data.processor or "Não disponível",
            texts.plural(threads, "processador lógico", "processadores lógicos") if threads else "",
        )
        memory = data.memory
        if memory is None:
            tiles["memory"].set("Não disponível")
        else:
            installed = memory.installed_bytes or memory.total_bytes
            tiles["memory"].set(
                format_bytes(installed),
                f"{format_bytes(memory.total_bytes)} utilizáveis · "
                f"{memory.used_percent:.0f}% em uso",
                bar=memory.used_percent / 100,
                bar_color=theme.NEUTRAL,
            )
        disk = info.disk
        if disk is None:
            tiles["disk"].set("Não disponível", level="warning")
        else:
            low = data.disk.status is Status.WARNING
            tiles["disk"].set(
                f"{format_bytes(disk.free_bytes)} livres",
                f"Unidade {disk.drive} · {disk.used_percent:.0f}% usado de "
                f"{format_bytes(disk.total_bytes)}",
                level="warning" if low else None,
                bar=disk.used_percent / 100,
                bar_color=theme.YELLOW if low else theme.GREEN,
            )
        tiles["uptime"].set(format_uptime(info.uptime_seconds), "Desde a última inicialização")

    def _show_maintenance(self, data: model.DashboardData) -> None:
        tiles = self.tiles
        privileges = data.privileges
        if privileges.is_admin:
            tiles["privileges"].set(
                "Administrador", "Todas as rotinas estão disponíveis", level="ok"
            )
        else:
            tiles["privileges"].set(
                "Administrador sem elevação" if privileges.can_elevate else "Usuário padrão",
                "Algumas rotinas exigem privilégios de administrador",
            )
        if data.reboot.required:
            tiles["reboot"].set(
                "Sim", texts.sentence(texts.reboot_sources(data.reboot.sources)), level="warning"
            )
        else:
            tiles["reboot"].set("Não", "Nenhuma reinicialização necessária", level="ok")
        last = data.last_run
        if last is None:
            tiles["last_run"].set("Nenhuma registrada", "Execute uma manutenção para registrar")
        else:
            label, level = texts.history_result(last.result)
            tiles["last_run"].set(label, format_datetime(last.started_at), level=level)
        chrome = data.chrome
        if chrome.installed:
            scope = (
                "Instalado para todos os usuários"
                if chrome.scope == SCOPE_MACHINE
                else "Instalado para o usuário atual"
            )
            tiles["chrome"].set(f"Versão {chrome.version or 'desconhecida'}", scope)
        else:
            tiles["chrome"].set("Não instalado")
        if data.winget is None:
            tiles["winget"].set(
                "Não encontrado", "Necessário para atualizar o Chrome", level="warning"
            )
        else:
            version = f"Versão {data.winget.version}" if data.winget.version else ""
            tiles["winget"].set("Disponível", version, level="ok")

    def show_config_state(self) -> None:
        """Reavalia o status geral após mudanças na configuração."""
        if self._loaded:
            self._show_status(self.data)
