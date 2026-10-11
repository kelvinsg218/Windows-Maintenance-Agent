"""Execução das operações demoradas fora da thread da interface (a janela não congela).

A thread de trabalho nunca toca na interface: ela apenas publica eventos em uma fila,
que a thread principal consome periodicamente com ``after``.

Enquanto a operação roda, uma thread auxiliar posta uma mensagem vazia (WM_NULL) para a
janela a cada segundo. No Windows, capturas externas da janela (``PrintWindow``) podem
fazer o laço de eventos do Tk perder o próximo temporizador e aguardar indefinidamente
por uma mensagem; sem esse aviso, o andamento só voltaria a ser exibido quando o
usuário movesse o mouse sobre a janela.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from typing import Any

from maintenance.gui import winapi

Emit = Callable[[str, Any], None]

WAKE_INTERVAL_SECONDS = 1.0
_DONE = "__done__"
_ERROR = "__error__"


def _window_handle(root: tk.Misc) -> int | None:
    """HWND da janela de nível superior (chamado na thread da interface)."""
    try:
        return int(root.winfo_toplevel().wm_frame(), 16)
    except (tk.TclError, ValueError):
        return None


class BackgroundTask:
    POLL_MS = 80

    def __init__(
        self,
        root: tk.Misc,
        name: str,
        job: Callable[[Emit], Any],
        *,
        on_event: Callable[[str, Any], None] | None = None,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException, str], None] | None = None,
    ) -> None:
        self.name = name
        self._root = root
        self._job = job
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._on_event = on_event
        self._on_done = on_done
        self._on_error = on_error
        self._thread = threading.Thread(target=self._run, name=f"wma-gui-{name}", daemon=True)
        self.finished = False
        self.started_at = time.monotonic()

    @property
    def running(self) -> bool:
        return not self.finished

    @property
    def thread_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self.started_at = time.monotonic()
        self._thread.start()
        handle = _window_handle(self._root)
        if handle:
            threading.Thread(
                target=self._keep_awake,
                args=(handle,),
                name=f"wma-gui-{self.name}-wake",
                daemon=True,
            ).start()
        self._root.after(self.POLL_MS, self._poll)

    def _keep_awake(self, handle: int) -> None:
        while not self.finished and winapi.wake_event_loop(handle):
            time.sleep(WAKE_INTERVAL_SECONDS)

    def emit(self, kind: str, payload: Any = None) -> None:
        """Chamado pela thread de trabalho."""
        self._queue.put((kind, payload))

    def _run(self) -> None:
        try:
            result = self._job(self.emit)
        except BaseException as exc:  # repassada à interface, que exibe a mensagem
            self._queue.put((_ERROR, (exc, traceback.format_exc())))
        else:
            self._queue.put((_DONE, result))

    def _poll(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == _DONE:
                self.finished = True
                if self._on_done:
                    self._on_done(payload)
                return
            if kind == _ERROR:
                self.finished = True
                if self._on_error:
                    self._on_error(*payload)
                return
            if self._on_event:
                self._on_event(kind, payload)
        try:
            self._root.after(self.POLL_MS, self._poll)
        except tk.TclError:
            pass  # janela já fechada
