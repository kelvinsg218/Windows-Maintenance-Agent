"""Testes da janela (Tkinter) com o ambiente simulado: nenhuma alteração no sistema real.

A janela principal permanece oculta; as caixas de diálogo são substituídas por funções
que registram a pergunta e devolvem a resposta definida pelo teste.
"""

import re
import time
import tkinter as tk

import pytest

from maintenance import APP_NAME
from maintenance.config import ConfigError, LoadedConfig
from maintenance.gui import app as app_module
from maintenance.gui import page_check, page_history, page_maintenance, texts, winapi
from maintenance.gui.app import MaintenanceApp
from maintenance.history import HistoryStore
from maintenance.system_info import MemoryStatus
from tests.fake_env import GB, build_environment
from tests.wua_fakes import FakeUpdate


class Dialogs:
    """Substitui confirm/inform e registra o que seria exibido ao usuário."""

    def __init__(self):
        self.answer = True
        self.asked = []
        self.informed = []

    def confirm(self, parent, fonts, title, message, **options):
        self.asked.append((title, message))
        return self.answer

    def inform(self, parent, fonts, title, message, **options):
        self.informed.append((title, message, options.get("kind", "info")))


@pytest.fixture
def dialogs(monkeypatch):
    fake = Dialogs()
    for module in (app_module, page_check, page_history, page_maintenance):
        if hasattr(module, "confirm"):
            monkeypatch.setattr(module, "confirm", fake.confirm)
        if hasattr(module, "inform"):
            monkeypatch.setattr(module, "inform", fake.inform)
    opened = []
    monkeypatch.setattr(winapi, "open_path", lambda path: opened.append(path) or True)
    fake.opened = opened
    return fake


@pytest.fixture
def make_root(request):
    """Cria janelas Tk ocultas, destruídas ao fim do teste.

    O Tcl associa seus canais padrão aos descritores 0/1/2 ao criar o interpretador; com
    a captura do pytest ativa eles apontam para arquivos temporários que o pytest troca,
    o que corrompe a leitura dos scripts do Tcl. Por isso a criação ocorre sem captura.
    """
    capture = request.config.pluginmanager.getplugin("capturemanager")
    created = []

    def factory():
        with capture.global_and_fixture_disabled():
            try:
                root = tk.Tk()
            except tk.TclError as exc:  # pragma: no cover - ambiente sem interface gráfica
                pytest.skip(f"Tk indisponível: {exc}")
        root.withdraw()
        created.append(root)
        return root

    yield factory
    for root in created:
        try:
            root.destroy()
        except tk.TclError:
            pass


def make_app(root, env, **options):
    defaults = {
        "services_factory": lambda: env.services,
        "config_loader": lambda: LoadedConfig(env.config, None, []),
        "memory_status": lambda: MemoryStatus(8 * GB, 3 * GB, 8 * GB),
        "cpu_count": lambda: 4,
    }
    defaults.update(options)
    return MaintenanceApp(root, **defaults)


@pytest.fixture
def gui(tmp_path, dialogs, make_root):
    env = build_environment(tmp_path, admin=False)
    return make_app(make_root(), env), env


def pump(app, condition, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.root.update()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("a interface não concluiu a operação a tempo")


def widget_texts(widget):
    found = []
    try:
        text = widget.cget("text")
    except tk.TclError:
        text = ""
    if isinstance(text, str) and text.strip():
        found.append(text)
    for child in widget.winfo_children():
        found += widget_texts(child)
    return found


def test_dashboard_shows_real_status_data(gui):
    app, env = gui
    dashboard = app.pages[texts.PAGE_DASHBOARD]
    pump(app, lambda: dashboard.data is not None)
    assert dashboard.data.info.hostname == "TI-04"
    shown = widget_texts(dashboard)
    assert "TI-04" in shown
    assert "Windows 11 Pro" in shown
    assert "Versão 153.0.8010.53" in shown
    assert "Nenhum problema detectado" in shown
    assert "Usuário padrão" in shown


def test_check_runs_in_background_and_changes_nothing(gui, dialogs):
    app, env = gui
    app.show_page(texts.PAGE_CHECK)
    check = app.pages[texts.PAGE_CHECK]
    check.start()
    assert app.busy
    pump(app, lambda: not app.busy)
    assert check._outcome is not None
    assert len(check._outcome.results) == 7
    assert env.old_file.exists() and env.wua.installed == []
    shown = widget_texts(check)
    assert "Resultados da verificação" in shown
    assert "2 pontos de atenção encontrados" in shown
    assert "Corrigir na Manutenção" in shown
    assert "Corrigível na Manutenção" in shown
    assert "Requer administrador" in shown
    assert dialogs.informed == []


def test_fixable_problems_are_preselected_in_maintenance(gui):
    app, env = gui
    check = app.pages[texts.PAGE_CHECK]
    check.start()
    pump(app, lambda: not app.busy)
    check._open_maintenance(["chrome", "cleanup"])
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    assert app.current_page == texts.PAGE_MAINTENANCE
    assert maintenance._selected() == ["chrome", "cleanup"]
    assert not app.busy  # nada é executado sem confirmação


def test_maintenance_requires_selection_and_confirmation(gui, dialogs):
    app, env = gui
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    assert not maintenance._run_button.enabled
    maintenance.preselect(["cleanup"])
    assert maintenance._run_button.enabled

    dialogs.answer = False
    maintenance.start()
    assert not app.busy and env.old_file.exists()
    title, message = dialogs.asked[-1]
    assert title == "Confirmar manutenção"
    assert "Limpeza de arquivos temporários" in message
    assert "não será reiniciado automaticamente" in message
    assert "Sem privilégios de administrador" in message

    dialogs.answer = True
    maintenance.start()
    pump(app, lambda: not app.busy)
    assert not env.old_file.exists() and env.recent_file.exists()
    shown = widget_texts(maintenance)
    assert "Manutenção concluída com pendências" in shown
    assert "Resultado de cada etapa" in shown
    assert "Abrir relatório" in shown
    entry = HistoryStore(env.paths.history_db).list_runs()[0]
    assert entry.tasks == "cleanup" and not entry.dry_run


def test_dry_run_simulates_without_changes(gui, dialogs):
    app, env = gui
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    maintenance.preselect(["chrome", "windows_update", "cleanup"])
    maintenance._dry_run.set(True)
    assert maintenance._run_button._text.cget("text") == "Simular manutenção"
    maintenance.start()
    assert dialogs.asked[-1][0] == "Confirmar simulação"
    pump(app, lambda: not app.busy)
    assert env.old_file.exists()
    assert "upgrade" not in env.runner.subcommands() and env.wua.installed == []
    shown = " ".join(widget_texts(maintenance))
    assert "Simulação concluída" in shown
    assert "nenhuma alteração foi feita" in shown
    assert "dry-run" not in shown


def test_history_lists_runs_with_details(gui, dialogs):
    app, env = gui
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    maintenance.preselect(["cleanup"])
    maintenance.start()
    pump(app, lambda: not app.busy)
    history = app.pages[texts.PAGE_HISTORY]
    app.show_page(texts.PAGE_HISTORY)
    pump(app, lambda: bool(history._entries))
    pump(app, lambda: history._detail.winfo_manager() == "pack")
    row = history._tree.item(history._tree.get_children()[0], "values")
    assert row[0] == "#1" and row[3] == "Sucesso com pendências"
    assert row[4] == "Limpeza de temporários" and row[6] == "Normal"
    shown = widget_texts(history._detail)
    assert "Manutenção nº 1" in shown
    assert "Limpeza de arquivos temporários" in shown


def test_empty_history(gui):
    app, env = gui
    history = app.pages[texts.PAGE_HISTORY]
    app.show_page(texts.PAGE_HISTORY)
    pump(app, lambda: not history._loading)
    assert "Nenhuma manutenção registrada" in widget_texts(history)


def test_only_one_operation_at_a_time(tmp_path, dialogs, make_root):
    env = build_environment(tmp_path, wua_options={"search_delay": 1.0})
    app = make_app(make_root(), env)
    app.pages[texts.PAGE_CHECK].start()
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    maintenance.preselect(["cleanup"])
    assert not maintenance._run_button.enabled
    maintenance.start()  # botão desabilitado; mesmo chamado diretamente é recusado
    assert dialogs.informed[-1][0] == "Operação em andamento"
    pump(app, lambda: not app.busy)
    assert maintenance._run_button.enabled
    assert env.old_file.exists()


def test_closing_during_maintenance_asks_first(tmp_path, dialogs, make_root):
    env = build_environment(tmp_path, wua_options={"search_delay": 1.5})
    root = make_root()
    app = make_app(root, env)
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    maintenance.preselect(["windows_update"])
    maintenance.start()
    dialogs.answer = False
    app.close()
    assert dialogs.asked[-1][0] == "Manutenção em andamento"
    assert not app.closing and root.winfo_exists()
    dialogs.answer = True
    app.close()
    assert app.closing
    assert app.has_live_threads  # main() encerra o processo sem esperar a thread


def test_elevation_request(gui, dialogs, monkeypatch):
    app, env = gui
    requested = []
    monkeypatch.setattr(winapi, "relaunch_as_admin", lambda page: requested.append(page) or False)
    app.show_page(texts.PAGE_MAINTENANCE)
    app.request_elevation()
    assert requested == [texts.PAGE_MAINTENANCE]
    assert dialogs.informed[-1][0] == "Privilégios não concedidos"
    assert not app.closing

    monkeypatch.setattr(winapi, "relaunch_as_admin", lambda page: True)
    app.request_elevation()
    assert app.closing  # a nova instância elevada assume; esta janela é fechada


def test_invalid_config_blocks_operations(tmp_path, dialogs, make_root):
    env = build_environment(tmp_path)

    def broken():
        raise ConfigError("'update_chrome' deve ser true ou false.")

    app = make_app(make_root(), env, config_loader=broken)
    assert app.config_error
    app.pages[texts.PAGE_CHECK].start()
    assert not app.busy
    assert dialogs.informed[-1][0] == "Erro no arquivo de configuração"
    assert "Erro no arquivo de configuração" in widget_texts(app.config_banner)


def test_admin_session_hides_elevation(tmp_path, dialogs, make_root):
    env = build_environment(tmp_path, admin=True)
    app = make_app(make_root(), env)
    assert not hasattr(app, "elevate_button")
    shown = widget_texts(app.pages[texts.PAGE_MAINTENANCE])
    assert "Sem privilégios de administrador" not in shown
    assert "Requer administrador" in shown  # o selo continua identificando a rotina


_ENGLISH = re.compile(
    r"\b(Error|Warning|Success|Failed|Skipped|SUCCESS|WARNING|FAILED|SKIPPED|Check|"
    r"Run|Report|History|Settings|Loading|Cancel|Close|Dashboard|Maintenance|Scan|Update|"
    r"dry-run|Unknown|None|True|False)\b"
)
_ALLOWED = (APP_NAME, "Maintenance Agent", "Windows Update")


def test_visible_texts_are_in_portuguese(tmp_path, dialogs, make_root):
    env = build_environment(
        tmp_path,
        wua_updates=[FakeUpdate("Cumulativa (KB5065789)"), FakeUpdate("Com licença", eula=False)],
    )
    root = make_root()
    app = make_app(root, env)
    dashboard = app.pages[texts.PAGE_DASHBOARD]
    pump(app, lambda: dashboard.data is not None)
    app.pages[texts.PAGE_CHECK].start()
    pump(app, lambda: not app.busy)
    maintenance = app.pages[texts.PAGE_MAINTENANCE]
    maintenance.preselect(["chrome", "windows_update", "cleanup"])
    maintenance._dry_run.set(True)
    maintenance.start()
    pump(app, lambda: not app.busy)
    history = app.pages[texts.PAGE_HISTORY]
    app.show_page(texts.PAGE_HISTORY)
    pump(app, lambda: history._detail.winfo_manager() == "pack")
    for text in widget_texts(root):
        cleaned = text
        for allowed in _ALLOWED:
            cleaned = cleaned.replace(allowed, "")
        assert not _ENGLISH.search(cleaned), text


def test_startup_failure_shows_a_message_instead_of_vanishing(monkeypatch, tmp_path):
    class DummyRoot:
        def __init__(self, **options):
            pass

        def withdraw(self):
            pass

    def broken_app(*args, **kwargs):
        raise RuntimeError("falha simulada")

    shown = []
    monkeypatch.setattr(app_module.tk, "Tk", DummyRoot)
    monkeypatch.setattr(app_module, "MaintenanceApp", broken_app)
    monkeypatch.setattr(app_module.winapi, "enable_dpi_awareness", lambda: None)
    monkeypatch.setattr(app_module.winapi, "set_app_user_model_id", lambda: None)
    monkeypatch.setattr(app_module.winapi, "show_error", lambda *args: shown.append(args))
    monkeypatch.setattr(app_module, "resolve_paths", lambda config: _paths_in(tmp_path))
    assert app_module.main([]) == 1
    title, message = shown[0]
    assert title == APP_NAME
    assert message.startswith("Não foi possível abrir o aplicativo.")
    assert "falha simulada" in message
    assert "RuntimeError" in (tmp_path / "logs" / app_module.GUI_ERROR_LOG).read_text("utf-8")


def _paths_in(base):
    from maintenance.config import AppPaths

    return AppPaths.from_base(base)


def test_page_argument():
    assert app_module._parse_args(["--page", "maintenance"]).page == "maintenance"
    assert app_module._parse_args([]).page == texts.PAGE_DASHBOARD
    assert app_module._parse_args(["--outro", "x"]).page == texts.PAGE_DASHBOARD


def test_event_loop_is_kept_awake_while_a_task_runs(make_root, monkeypatch):
    from maintenance.gui import tasks

    posted = []
    monkeypatch.setattr(tasks, "WAKE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(
        tasks.winapi, "wake_event_loop", lambda handle: posted.append(handle) or True
    )
    root = make_root()
    done = []

    def job(emit):
        time.sleep(0.4)
        return "ok"

    task = tasks.BackgroundTask(root, "teste", job, on_done=done.append)
    task.start()
    deadline = time.monotonic() + 10
    while not done and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert done == ["ok"]
    assert len(posted) >= 2 and len(set(posted)) == 1  # sempre a mesma janela
    count = len(posted)
    time.sleep(0.3)
    assert len(posted) <= count + 1  # parou após a conclusão


def test_keep_awake_stops_when_the_window_is_gone(make_root, monkeypatch):
    from maintenance.gui import tasks

    calls = []
    monkeypatch.setattr(tasks, "WAKE_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(
        tasks.winapi, "wake_event_loop", lambda handle: calls.append(handle) and False
    )
    task = tasks.BackgroundTask(make_root(), "teste", lambda emit: None)
    task._keep_awake(123)  # retorna imediatamente: a janela não existe mais
    assert calls == [123]
