"""Janela principal da interface gráfica: barra lateral, páginas e operações em segundo plano."""

from __future__ import annotations

import argparse
import os
import tkinter as tk
import traceback
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from maintenance import APP_NAME, __version__
from maintenance.config import AppConfig, ConfigError, LoadedConfig, load_config, resolve_paths
from maintenance.gui import texts, theme, winapi
from maintenance.gui.dialogs import confirm, inform
from maintenance.gui.page_base import Page
from maintenance.gui.page_check import CheckPage
from maintenance.gui.page_dashboard import DashboardPage
from maintenance.gui.page_history import HistoryPage
from maintenance.gui.page_maintenance import MaintenancePage
from maintenance.gui.tasks import BackgroundTask, Emit
from maintenance.gui.theme import Glyph, px
from maintenance.gui.widgets import Banner, Button, NavItem, WrapLabel, route_mousewheel
from maintenance.logging_service import close_logging
from maintenance.privileges import PrivilegeInfo
from maintenance.runner import CriticalError, Services
from maintenance.system_info import MemoryStatus, get_memory_status

GUI_ERROR_LOG = "gui_errors.log"

_NAVIGATION: tuple[tuple[str, str], ...] = (
    (texts.PAGE_DASHBOARD, Glyph.HOME),
    (texts.PAGE_CHECK, Glyph.DIAGNOSTIC),
    (texts.PAGE_MAINTENANCE, Glyph.WRENCH),
    (texts.PAGE_HISTORY, Glyph.HISTORY),
)
_OPERATION_NAMES = {texts.PAGE_CHECK: "verificação", texts.PAGE_MAINTENANCE: "manutenção"}


class MaintenanceApp:
    """Estado compartilhado entre as páginas e controle das operações em andamento."""

    def __init__(
        self,
        root: tk.Tk,
        *,
        services_factory: Callable[[], Services] = Services,
        config_loader: Callable[[], LoadedConfig] | None = None,
        memory_status: Callable[[], MemoryStatus | None] = get_memory_status,
        cpu_count: Callable[[], int | None] = os.cpu_count,
        initial_page: str = texts.PAGE_DASHBOARD,
    ) -> None:
        self.root = root
        self.fonts = theme.init(root)
        self.services_factory = services_factory
        self.memory_status = memory_status
        self.cpu_count = cpu_count
        self._config_loader = config_loader or (lambda: load_config(None))
        self.config = AppConfig()
        self.paths = resolve_paths(self.config)
        self.config_error: str | None = None
        self.config_notes: list[str] = []
        self.operation: BackgroundTask | None = None
        self._background: list[BackgroundTask] = []
        self.closing = False
        self.privileges: PrivilegeInfo = services_factory().get_privileges()
        self.pages: dict[str, Page] = {}
        self.nav: dict[str, NavItem] = {}
        self.current_page = initial_page

        root.title(APP_NAME)
        self._set_icon()
        self._apply_config()
        self._build()
        self._update_config_banner()
        route_mousewheel(root)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._report_callback_exception
        root.bind("<F5>", lambda _event: self.pages[self.current_page].refresh())
        self.show_page(initial_page if initial_page in self.pages else texts.PAGE_DASHBOARD)

    # --- Construção ---------------------------------------------------------------------

    def _set_icon(self) -> None:
        if winapi.ICON_FILE.is_file():
            try:
                self.root.iconbitmap(default=str(winapi.ICON_FILE))
            except tk.TclError:
                pass

    def _build(self) -> None:
        root = self.root
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(1, weight=1)
        sidebar = tk.Frame(root, bg=theme.SIDEBAR, width=px(248))
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.pack_propagate(False)
        tk.Frame(root, bg=theme.BORDER, width=1).grid(row=0, column=0, sticky="nse")
        self._build_sidebar(sidebar)

        content = tk.Frame(root, bg=theme.BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(1, weight=1)
        content.grid_columnconfigure(0, weight=1)
        self.config_banner = Banner(content, self.fonts)
        container = tk.Frame(content, bg=theme.BG)
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        for page_class in (DashboardPage, CheckPage, MaintenancePage, HistoryPage):
            page = page_class(container, self)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[page.name] = page

    def _build_sidebar(self, sidebar: tk.Frame) -> None:
        fonts = self.fonts
        brand = tk.Frame(sidebar, bg=theme.SIDEBAR)
        brand.pack(fill="x", padx=px(18), pady=(px(22), px(24)))
        self._logo = self._load_logo()
        if self._logo is not None:
            tk.Label(brand, image=self._logo, bg=theme.SIDEBAR).pack(side="left", padx=(0, px(10)))
        names = tk.Frame(brand, bg=theme.SIDEBAR)
        names.pack(side="left", fill="x", expand=True)
        tk.Label(
            names, text="Windows", font=fonts.h3, fg=theme.TEXT, bg=theme.SIDEBAR, anchor="w"
        ).pack(fill="x")
        tk.Label(
            names,
            text="Maintenance Agent",
            font=fonts.small,
            fg=theme.TEXT_MUTED,
            bg=theme.SIDEBAR,
            anchor="w",
        ).pack(fill="x")

        for name, icon in _NAVIGATION:
            item = NavItem(
                sidebar,
                fonts,
                texts.PAGE_TITLES[name],
                icon,
                lambda page=name: self.show_page(page),
            )
            item.pack(fill="x", padx=px(10), pady=px(2))
            self.nav[name] = item

        footer = tk.Frame(sidebar, bg=theme.SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=px(18), pady=px(18))
        status = tk.Frame(footer, bg=theme.SIDEBAR)
        status.pack(fill="x")
        admin = self.privileges.is_admin
        tk.Label(
            status,
            text=fonts.glyph(Glyph.SHIELD),
            font=fonts.icon,
            fg=theme.GREEN if admin else theme.TEXT_FAINT,
            bg=theme.SIDEBAR,
        ).pack(side="left", padx=(0, px(8)))
        tk.Label(
            status,
            text="Administrador" if admin else "Sem privilégios de administrador",
            font=fonts.small,
            fg=theme.TEXT if admin else theme.TEXT_MUTED,
            bg=theme.SIDEBAR,
            anchor="w",
        ).pack(side="left", fill="x")
        if not admin:
            WrapLabel(
                footer,
                text="Algumas rotinas de manutenção precisam de privilégios elevados.",
                font=fonts.small,
                fg=theme.TEXT_FAINT,
                wraplength=px(200),
            ).pack(fill="x", pady=(px(6), px(8)))
            self.elevate_button = Button(
                footer,
                fonts,
                "Reiniciar como administrador",
                self.request_elevation,
                icon=Glyph.ADMIN,
                small=True,
            )
            self.elevate_button.pack(fill="x")
        tk.Label(
            footer,
            text=f"Versão {__version__}",
            font=fonts.small,
            fg=theme.TEXT_FAINT,
            bg=theme.SIDEBAR,
            anchor="w",
        ).pack(fill="x", pady=(px(14), 0))

    def _load_logo(self) -> tk.PhotoImage | None:
        if not winapi.LOGO_FILE.is_file():
            return None
        try:
            image = tk.PhotoImage(master=self.root, file=str(winapi.LOGO_FILE))
        except tk.TclError:
            return None
        factor = max(round(image.width() / px(34)), 1)
        return image.subsample(factor) if factor > 1 else image

    def present(self) -> None:
        """Centraliza, aplica a barra de título escura e exibe a janela com um leve fade-in."""
        root = self.root
        root.update_idletasks()
        area = winapi.work_area() or (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        left, top, right, bottom = area
        available_w, available_h = right - left, bottom - top
        frame = px(40)  # barra de título e bordas da janela
        width = min(px(1200), available_w - px(24))
        height = min(px(780), available_h - frame - px(16))
        root.minsize(min(px(920), width), min(px(600), height))
        x = left + (available_w - width) // 2
        y = top + max((available_h - height - frame) // 2, 0)
        root.geometry(f"{width}x{height}+{x}+{y}")
        try:
            winapi.use_dark_title_bar(int(root.wm_frame(), 16))
        except (ValueError, tk.TclError):
            pass
        try:
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        root.deiconify()
        self._fade_in(0)

    def _fade_in(self, step: int) -> None:
        steps = 8
        try:
            self.root.attributes("-alpha", min((step + 1) / steps, 1.0))
        except tk.TclError:
            return
        if step + 1 < steps:
            self.root.after(20, self._fade_in, step + 1)

    # --- Navegação e configuração -------------------------------------------------------

    def show_page(self, name: str) -> None:
        self.current_page = name
        for page_name, item in self.nav.items():
            item.set_active(page_name == name)
        page = self.pages[name]
        page.tkraise()
        page.on_show()

    def _apply_config(self) -> None:
        try:
            loaded = self._config_loader()
        except ConfigError as exc:
            self.config_error = str(exc)
            self.config = AppConfig()
            self.paths = resolve_paths(self.config)
            self.config_notes = []
        else:
            self.config_error = None
            self.config = loaded.config
            self.paths = resolve_paths(loaded.config)
            # Sem config.json o aplicativo usa os padrões: situação normal, sem aviso.
            self.config_notes = list(loaded.notes) if loaded.path is not None else []

    def reload_config(self) -> bool:
        """Relê o config.json (como a CLI faz a cada comando). ``False`` = configuração inválida."""
        had_error = self.config_error
        self._apply_config()
        self._update_config_banner()
        if had_error != self.config_error:
            self.pages[texts.PAGE_DASHBOARD].refresh()
        return self.config_error is None

    def _update_config_banner(self) -> None:
        banner = self.config_banner
        if self.config_error:
            banner.show(
                "error",
                "Erro no arquivo de configuração",
                f"{self.config_error} A verificação e a manutenção ficam indisponíveis até que "
                "o arquivo config.json seja corrigido.",
            )
        elif self.config_notes:
            banner.show("warning", "Avisos de configuração", "\n".join(self.config_notes))
        else:
            banner.grid_remove()
            return
        banner.grid(row=0, column=0, sticky="ew", padx=px(32), pady=(px(20), 0))

    def require_valid_config(self) -> bool:
        if self.reload_config():
            return True
        inform(
            self.root,
            self.fonts,
            "Erro no arquivo de configuração",
            f"{self.config_error}\n\nCorrija o arquivo config.json e tente novamente.",
            kind="error",
        )
        return False

    # --- Operações em segundo plano -----------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.operation is not None and self.operation.running

    def start_operation(
        self,
        name: str,
        job: Callable[[Emit], Any],
        *,
        on_event: Callable[[str, Any], None],
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException, str], None],
    ) -> bool:
        """Inicia a verificação ou a manutenção (somente uma operação por vez)."""
        current = self.operation
        if current is not None and current.running:
            running = _OPERATION_NAMES.get(current.name, "operação")
            inform(
                self.root,
                self.fonts,
                "Operação em andamento",
                f"Aguarde a conclusão da {running} em andamento antes de iniciar outra.",
            )
            return False

        def finished(callback: Callable[..., None]) -> Callable[..., None]:
            def wrapper(*args: Any) -> None:
                self.nav[name].set_busy(False)
                try:
                    callback(*args)
                finally:
                    self._notify_busy_changed()

            return wrapper

        task = BackgroundTask(
            self.root,
            name,
            job,
            on_event=on_event,
            on_done=finished(on_done),
            on_error=finished(on_error),
        )
        self.operation = task
        self.nav[name].set_busy(True)
        task.start()
        self._notify_busy_changed()
        return True

    def _notify_busy_changed(self) -> None:
        for page in self.pages.values():
            page.on_busy_changed()

    def run_background(
        self,
        name: str,
        job: Callable[[Emit], Any],
        *,
        on_done: Callable[[Any], None],
        on_error: Callable[[BaseException, str], None],
    ) -> None:
        """Consultas rápidas (painel, histórico) que podem rodar junto com uma operação."""
        self._background = [task for task in self._background if task.running]
        task = BackgroundTask(self.root, name, job, on_done=on_done, on_error=on_error)
        self._background.append(task)
        task.start()

    @property
    def has_live_threads(self) -> bool:
        tasks = [*self._background, *([self.operation] if self.operation else [])]
        return any(task.thread_alive for task in tasks)

    def describe_error(self, exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, ConfigError):
            return "Erro de configuração", str(exc)
        if isinstance(exc, CriticalError):
            return "Erro crítico", f"Não foi possível concluir a operação: {exc}."
        return "Erro inesperado", f"{type(exc).__name__}: {exc}"

    def log_error(self, detail: str) -> None:
        """Registra detalhes técnicos de erros inesperados da interface."""
        try:
            self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
            with (self.paths.logs_dir / GUI_ERROR_LOG).open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{detail}\n")
        except OSError:
            pass

    def _report_callback_exception(self, exc_type: type, exc: BaseException, tb: Any) -> None:
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        self.log_error(detail)
        if self.closing:
            return
        try:
            inform(
                self.root,
                self.fonts,
                "Erro inesperado",
                f"Ocorreu um erro inesperado na interface: {exc}",
                kind="error",
                detail=detail,
            )
        except tk.TclError:
            pass

    # --- Elevação e encerramento --------------------------------------------------------

    def request_elevation(self) -> None:
        if self.busy:
            inform(
                self.root,
                self.fonts,
                "Operação em andamento",
                "Aguarde a conclusão da operação em andamento antes de reiniciar o aplicativo.",
            )
            return
        if winapi.relaunch_as_admin(self.current_page):
            self.closing = True
            self.root.destroy()
            return
        inform(
            self.root,
            self.fonts,
            "Privilégios não concedidos",
            "O Windows não concedeu privilégios de administrador (o pedido foi cancelado ou "
            "não é permitido para esta conta). O aplicativo continua aberto sem privilégios "
            "de administrador.",
            kind="warning",
        )

    def close(self) -> None:
        current = self.operation
        if current is not None and current.running:
            if current.name == texts.PAGE_MAINTENANCE:
                leave = confirm(
                    self.root,
                    self.fonts,
                    "Manutenção em andamento",
                    "Se você fechar agora, a manutenção será interrompida. Etapas já iniciadas "
                    "pelo Windows (como a instalação de atualizações) podem continuar em "
                    "segundo plano.\n\nDeseja realmente sair?",
                    confirm_label="Sair mesmo assim",
                    cancel_label="Continuar aguardando",
                    kind="warning",
                    default_cancel=True,
                )
            else:
                leave = confirm(
                    self.root,
                    self.fonts,
                    "Verificação em andamento",
                    "A verificação em andamento será interrompida. Deseja sair?",
                    confirm_label="Sair",
                )
            if not leave:
                return
        self.closing = True
        self.root.destroy()


def _report_startup_error(exc: BaseException) -> None:
    detail = traceback.format_exc()
    try:
        logs_dir = resolve_paths(AppConfig()).logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        with (logs_dir / GUI_ERROR_LOG).open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{detail}\n")
    except OSError:
        pass
    winapi.show_error(
        APP_NAME,
        f"Não foi possível abrir o aplicativo.\n\n{type(exc).__name__}: {exc}",
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Windows Maintenance Agent", add_help=False)
    parser.add_argument(
        "--page",
        choices=[name for name, _icon in _NAVIGATION],
        default=texts.PAGE_DASHBOARD,
    )
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit:  # argumento inválido: abre a página inicial
        args = argparse.Namespace(page=texts.PAGE_DASHBOARD)
    winapi.enable_dpi_awareness()
    winapi.set_app_user_model_id()
    try:
        root = tk.Tk(className="WindowsMaintenanceAgent")
        root.withdraw()
        app = MaintenanceApp(root, initial_page=args.page)
        app.present()
    except Exception as exc:  # falha ao abrir a janela: avisa o usuário em vez de sumir
        _report_startup_error(exc)
        return 1
    root.mainloop()
    if app.has_live_threads:
        # Fechamento confirmado com uma operação em andamento: encerra o processo sem
        # aguardar as threads de trabalho (o histórico marca a execução como INTERRUPTED).
        close_logging()
        os._exit(0)
    return 0
