"""Windows Update via Windows Update Agent API (COM ``Microsoft.Update.Session``).

Estratégia: a API oficial e documentada da Microsoft, usada pelo próprio Windows,
acessada com pywin32. Ela respeita a configuração da máquina (WSUS, políticas de
grupo, Windows Update for Business).

Regras de segurança:
- pesquisa apenas atualizações de software não instaladas e não ocultas (sem drivers);
- por padrão ignora atualizações opcionais e de recurso (upgrade de versão do Windows);
- não aceita termos de licença (EULA) automaticamente, salvo configuração explícita;
- instala somente com privilégios administrativos; sem eles, apenas pesquisa;
- NUNCA reinicia o computador: apenas informa quando a reinicialização é necessária.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from maintenance.logging_service import SUCCESS
from maintenance.process import OperationTimeoutError, wait_for
from maintenance.results import ModuleResult, Status

logger = logging.getLogger(__name__)

MODULE = "windows_update"
CLIENT_APPLICATION_ID = "Windows Maintenance Agent"
SEARCH_CRITERIA = "IsInstalled=0 and IsHidden=0 and Type='Software'"
UPGRADES_CATEGORY_ID = "3689bdc8-b205-4af4-8d4a-a63924c5e9d5"

# OperationResultCode (WUA)
ORC_NOT_STARTED = 0
ORC_IN_PROGRESS = 1
ORC_SUCCEEDED = 2
ORC_SUCCEEDED_WITH_ERRORS = 3
ORC_FAILED = 4
ORC_ABORTED = 5
_SUCCEEDED_CODES = (ORC_SUCCEEDED, ORC_SUCCEEDED_WITH_ERRORS)

HRESULT_MESSAGES: dict[int, str] = {
    0x80070005: "acesso negado (requer privilégios administrativos)",
    0x80240044: "somente administradores podem executar esta operação",
    0x80070422: "o serviço Windows Update está desabilitado",
    0x8024001E: "operação interrompida: serviço ou sistema sendo desligado",
    0x80240016: "outra instalação em andamento ou reinicialização obrigatória pendente",
    0x8024000B: "operação cancelada",
    0x80240024: "nenhuma atualização para processar",
    0x8024402C: "não foi possível resolver o servidor de atualização (verifique rede/proxy)",
    0x80072EE2: "tempo limite de conexão com o servidor de atualização",
    0x80072EFD: "não foi possível conectar ao servidor de atualização",
    0x80244022: "serviço de atualização indisponível (HTTP 503)",
    0x80070643: "erro fatal durante a instalação",
    0x80073712: "repositório de componentes corrompido (DISM /RestoreHealth)",
}


class WindowsUpdateError(Exception):
    def __init__(self, message: str, hresult: int | None = None) -> None:
        super().__init__(message)
        self.hresult = hresult


@dataclass
class UpdateItem:
    update_id: str
    title: str
    kb_articles: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    size_bytes: int = 0
    severity: str = ""
    optional: bool = False
    downloaded: bool = False
    eula_accepted: bool = True
    requires_user_input: bool = False
    reboot_behavior: int = 0
    feature_upgrade: bool = False
    eligible: bool = False
    skip_reason: str = ""
    manual_action: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UpdateOperationResult:
    title: str
    result_code: int
    hresult: int

    @property
    def succeeded(self) -> bool:
        return self.result_code in _SUCCEEDED_CODES


@dataclass(frozen=True)
class OperationOutcome:
    result_code: int
    reboot_required: bool
    per_update: tuple[UpdateOperationResult, ...]


class WuaClient(Protocol):
    """Operações usadas pelo agente; implementada por :class:`WuaComClient` e por fakes."""

    def search(self, criteria: str) -> list[Any]: ...

    def installer_busy(self) -> bool: ...

    def reboot_required_before_installation(self) -> bool: ...

    def accept_eula(self, update: Any) -> None: ...

    def download(self, updates: list[Any]) -> OperationOutcome: ...

    def install(self, updates: list[Any]) -> OperationOutcome: ...


def com_items(collection: Any) -> list[Any]:
    return [collection.Item(index) for index in range(int(collection.Count))]


def _safe(read: Callable[[], Any], default: Any) -> Any:
    try:
        value = read()
    except Exception:  # propriedade COM indisponível
        return default
    return default if value is None else value


def update_item_from_com(update: Any) -> UpdateItem:
    categories = _safe(lambda: com_items(update.Categories), [])
    category_ids = {str(_safe(lambda c=c: c.CategoryID, "")).lower() for c in categories}
    behavior = _safe(lambda: update.InstallationBehavior, None)
    return UpdateItem(
        update_id=str(_safe(lambda: update.Identity.UpdateID, "")),
        title=str(_safe(lambda: update.Title, "(sem título)")),
        kb_articles=[f"KB{kb}" for kb in _safe(lambda: com_items(update.KBArticleIDs), [])],
        categories=[str(_safe(lambda c=c: c.Name, "")) for c in categories],
        size_bytes=int(_safe(lambda: update.MaxDownloadSize, 0)),
        severity=str(_safe(lambda: update.MsrcSeverity, "")),
        optional=bool(_safe(lambda: update.BrowseOnly, False)),
        downloaded=bool(_safe(lambda: update.IsDownloaded, False)),
        eula_accepted=bool(_safe(lambda: update.EulaAccepted, True)),
        requires_user_input=bool(_safe(lambda: behavior.CanRequestUserInput, False)),
        reboot_behavior=int(_safe(lambda: behavior.RebootBehavior, 0)),
        feature_upgrade=UPGRADES_CATEGORY_ID in category_ids,
    )


def classify(items: list[UpdateItem], *, include_optional: bool, accept_eulas: bool) -> None:
    """Define quais atualizações podem ser instaladas automaticamente."""
    for item in items:
        item.eligible, item.manual_action, item.skip_reason = False, False, ""
        if item.feature_upgrade:
            item.skip_reason = "atualização de recurso (nova versão do Windows)"
        elif item.optional and not include_optional:
            item.skip_reason = "atualização opcional"
        elif item.requires_user_input:
            item.skip_reason, item.manual_action = "requer interação do usuário", True
        elif not item.eula_accepted and not accept_eulas:
            item.skip_reason, item.manual_action = "requer aceite de termos de licença", True
        else:
            item.eligible = True


def hresult_from_exception(exc: BaseException) -> int | None:
    """Extrai o HRESULT de ``pywintypes.com_error`` (ou de :class:`WindowsUpdateError`)."""
    if isinstance(exc, WindowsUpdateError):
        return exc.hresult
    args = getattr(exc, "args", ())
    code: Any = None
    if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) >= 6 and args[2][5]:
        code = args[2][5]  # excepinfo.scode (erro real dentro de DISP_E_EXCEPTION)
    elif args and isinstance(args[0], int):
        code = args[0]
    return code & 0xFFFFFFFF if isinstance(code, int) else None


def describe_hresult(code: int) -> str:
    message = HRESULT_MESSAGES.get(code)
    return f"0x{code:08X}" + (f" ({message})" if message else "")


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, ImportError):
        return "componente pywin32 indisponível (reinstale o agente)"
    code = hresult_from_exception(exc)
    if code is not None:
        text = str(exc) if isinstance(exc, WindowsUpdateError) else ""
        return f"{text} {describe_hresult(code)}".strip()
    return str(exc) or type(exc).__name__


class WuaComClient:
    """Cliente real da Windows Update Agent API via pywin32."""

    def __init__(self) -> None:
        import win32com.client

        self._dispatch = win32com.client.Dispatch
        self._session = self._dispatch("Microsoft.Update.Session")
        try:
            # Identifica o agente nos logs do próprio Windows Update (atividade auditável).
            self._session.ClientApplicationID = CLIENT_APPLICATION_ID
        except Exception:
            logger.debug("Could not set ClientApplicationID")

    def search(self, criteria: str) -> list[Any]:
        result = self._session.CreateUpdateSearcher().Search(criteria)
        code = int(result.ResultCode)
        if code not in _SUCCEEDED_CODES:
            raise WindowsUpdateError(f"a pesquisa terminou com o código {code}")
        return com_items(result.Updates)

    def installer_busy(self) -> bool:
        return bool(self._session.CreateUpdateInstaller().IsBusy)

    def reboot_required_before_installation(self) -> bool:
        return bool(self._session.CreateUpdateInstaller().RebootRequiredBeforeInstallation)

    def accept_eula(self, update: Any) -> None:
        update.AcceptEula()

    def _collection(self, updates: list[Any]) -> Any:
        collection = self._dispatch("Microsoft.Update.UpdateColl")
        for update in updates:
            collection.Add(update)
        return collection

    def download(self, updates: list[Any]) -> OperationOutcome:
        downloader = self._session.CreateUpdateDownloader()
        downloader.Updates = self._collection(updates)
        return _outcome(downloader.Download(), updates, reboot_required=False)

    def install(self, updates: list[Any]) -> OperationOutcome:
        installer = self._session.CreateUpdateInstaller()
        installer.Updates = self._collection(updates)
        for name, value in (("AllowSourcePrompts", False), ("ForceQuiet", True)):
            try:
                setattr(installer, name, value)
            except Exception:
                logger.debug("Could not set installer.%s", name)
        result = installer.Install()
        return _outcome(result, updates, reboot_required=bool(result.RebootRequired))


def _outcome(result: Any, updates: list[Any], *, reboot_required: bool) -> OperationOutcome:
    per_update = []
    for index, update in enumerate(updates):
        item_result = result.GetUpdateResult(index)
        per_update.append(
            UpdateOperationResult(
                str(_safe(lambda u=update: u.Title, "")),
                int(item_result.ResultCode),
                int(item_result.HResult) & 0xFFFFFFFF,
            )
        )
    return OperationOutcome(int(result.ResultCode), reboot_required, tuple(per_update))


class _ComApartment:
    """Inicializa o COM na thread de trabalho (MTA) quando pywin32 está disponível."""

    def __enter__(self) -> _ComApartment:
        try:
            import pythoncom
        except ImportError:
            self._pythoncom = None
            return self
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        self._pythoncom = pythoncom
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()


class _Worker(threading.Thread):
    """Executa pesquisa (e, se solicitado, download/instalação) fora da thread principal.

    Objetos COM ficam restritos a esta thread; a thread principal recebe apenas dados
    simples e aguarda com timeouts separados para pesquisa e instalação.
    """

    def __init__(
        self,
        client_factory: Callable[[], WuaClient],
        *,
        install: bool,
        include_optional: bool,
        accept_eulas: bool,
    ) -> None:
        super().__init__(name="wma-windows-update", daemon=True)
        self._client_factory = client_factory
        self._install = install
        self._include_optional = include_optional
        self._accept_eulas = accept_eulas
        self.search_done = threading.Event()
        self.finished = threading.Event()
        self.items: list[UpdateItem] = []
        self.error: str | None = None
        self.blocked_reason: str | None = None
        self.download: OperationOutcome | None = None
        self.install: OperationOutcome | None = None
        self.not_downloaded: list[str] = []

    def run(self) -> None:
        try:
            with _ComApartment():
                try:
                    self._operations()
                except Exception as exc:  # guardamos só texto: nada de referências COM
                    self.error = describe_error(exc)
        except Exception as exc:
            self.error = describe_error(exc)
        finally:
            self.search_done.set()
            self.finished.set()

    def _operations(self) -> None:
        client = self._client_factory()
        updates = client.search(SEARCH_CRITERIA)
        items = [update_item_from_com(update) for update in updates]
        classify(items, include_optional=self._include_optional, accept_eulas=self._accept_eulas)
        self.items = items
        self.search_done.set()
        if not self._install:
            return
        selected = [(item, update) for item, update in zip(items, updates, strict=True)]
        selected = [(item, update) for item, update in selected if item.eligible]
        if not selected:
            return
        if client.installer_busy():
            self.blocked_reason = "outra instalação do Windows Update está em andamento"
            return
        if client.reboot_required_before_installation():
            self.blocked_reason = "há uma reinicialização pendente que impede novas instalações"
            return
        if self._accept_eulas:
            for item, update in selected:
                if not item.eula_accepted:
                    client.accept_eula(update)
        pending_download = [update for item, update in selected if not item.downloaded]
        if pending_download:
            logger.info("Downloading %d Windows updates", len(pending_download))
            self.download = client.download(pending_download)
        ready = [
            update
            for _item, update in selected
            if bool(_safe(lambda u=update: u.IsDownloaded, False))
        ]
        self.not_downloaded = [
            item.title
            for item, update in selected
            if not bool(_safe(lambda u=update: u.IsDownloaded, False))
        ]
        if ready:
            logger.info("Installing %d Windows updates", len(ready))
            self.install = client.install(ready)


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def process_windows_update(
    *,
    apply: bool,
    is_admin: bool,
    dry_run: bool = False,
    include_optional: bool = False,
    accept_eulas: bool = False,
    search_timeout: float = 1800,
    install_timeout: float = 10800,
    client_factory: Callable[[], WuaClient] = WuaComClient,
) -> ModuleResult:
    """Pesquisa (``apply=False``) ou pesquisa e instala (``apply=True``) atualizações."""
    logger.info("Checking Windows Update")
    install = apply and is_admin and not dry_run
    worker = _Worker(
        client_factory,
        install=install,
        include_optional=include_optional,
        accept_eulas=accept_eulas,
    )
    worker.start()
    try:
        wait_for(
            lambda: not worker.search_done.is_set(),
            search_timeout,
            name="Windows Update search",
            join=worker.search_done.wait,
        )
    except OperationTimeoutError:
        logger.error("Windows Update search timed out after %.0f s", search_timeout)
        return ModuleResult(
            MODULE,
            Status.FAILED,
            f"Tempo limite excedido na pesquisa do Windows Update ({search_timeout / 60:.0f} min)",
            errors=["timeout"],
        )
    if worker.error and not worker.items:
        logger.error("Windows Update search failed: %s", worker.error)
        return ModuleResult(
            MODULE,
            Status.FAILED,
            f"Falha ao pesquisar atualizações do Windows: {worker.error}",
            errors=[worker.error],
        )

    items = worker.items
    _log_items(items)
    details = _base_details(items)
    eligible = [item for item in items if item.eligible]
    if not install or not eligible:
        return _search_result(items, details, apply=apply, is_admin=is_admin, dry_run=dry_run)

    try:
        wait_for(
            lambda: not worker.finished.is_set(),
            install_timeout,
            name="Windows Update install",
            join=worker.finished.wait,
        )
    except OperationTimeoutError:
        logger.error("Windows Update installation timed out after %.0f s", install_timeout)
        return ModuleResult(
            MODULE,
            Status.FAILED,
            "Tempo limite excedido na instalação; o serviço Windows Update pode concluir a "
            "operação em segundo plano",
            details,
            errors=["timeout"],
        )
    return _install_result(worker, details)


def _log_items(items: list[UpdateItem]) -> None:
    eligible = sum(1 for item in items if item.eligible)
    logger.info(
        "Windows Update search found %d applicable updates (%d skipped)",
        eligible,
        len(items) - eligible,
    )
    for item in items:
        state = "pending" if item.eligible else f"skipped: {item.skip_reason}"
        logger.info("Update: %s [%s]", item.title, state)


def _base_details(items: list[UpdateItem]) -> dict[str, Any]:
    eligible = [item for item in items if item.eligible]
    return {
        "updates_available": len(eligible),
        "updates": [item.to_dict() for item in items],
        "skipped": sum(1 for item in items if not item.eligible),
        "manual_action": sum(1 for item in items if item.manual_action),
        "updates_installed": 0,
        "reboot_required": False,
    }


def _search_result(
    items: list[UpdateItem], details: dict[str, Any], *, apply: bool, is_admin: bool, dry_run: bool
) -> ModuleResult:
    eligible = [item for item in items if item.eligible]
    manual = [item for item in items if item.manual_action]
    ignored = len(items) - len(eligible) - len(manual)
    if not eligible:
        if manual:
            return ModuleResult(
                MODULE,
                Status.WARNING,
                f"{_plural(len(manual), 'atualização exige', 'atualizações exigem')} "
                "ação manual (EULA ou interação)",
                details,
            )
        suffix = f" ({ignored} opcionais/de recurso ignoradas)" if ignored else ""
        return ModuleResult(
            MODULE, Status.SUCCESS, f"Nenhuma atualização do Windows pendente{suffix}", details
        )
    one = len(eligible) == 1
    if not apply:
        summary = _plural(
            len(eligible),
            "atualização do Windows encontrada",
            "atualizações do Windows encontradas",
        )
    else:
        summary = _plural(len(eligible), "atualização pendente", "atualizações pendentes")
        if dry_run:
            summary += "; não instalada (dry-run)" if one else "; não instaladas (dry-run)"
        elif not is_admin:
            logger.warning("Windows Update installation requires administrative privileges")
            summary += ": a instalação requer privilégios administrativos"
    if manual:
        summary += f"; {_plural(len(manual), 'exige', 'exigem')} ação manual"
    return ModuleResult(MODULE, Status.WARNING, summary, details)


def _install_result(worker: _Worker, details: dict[str, Any]) -> ModuleResult:
    eligible = [item for item in worker.items if item.eligible]
    if worker.blocked_reason:
        logger.warning("Windows Update installation blocked: %s", worker.blocked_reason)
        return ModuleResult(
            MODULE,
            Status.WARNING,
            f"{_plural(len(eligible), 'atualização pendente', 'atualizações pendentes')}: "
            f"{worker.blocked_reason}",
            details,
        )
    outcome = worker.install
    if outcome is None:
        error = worker.error or "nenhuma atualização foi baixada"
        logger.error("Windows Update download failed: %s", error)
        return ModuleResult(
            MODULE,
            Status.FAILED,
            f"Falha ao baixar atualizações do Windows: {error}",
            details,
            errors=[error],
        )

    installed = [result for result in outcome.per_update if result.succeeded]
    failed = [result for result in outcome.per_update if not result.succeeded]
    errors = [f"{r.title}: {describe_hresult(r.hresult)}" for r in failed]
    errors += [f"{title}: download não concluído" for title in worker.not_downloaded]
    details.update(
        updates_installed=len(installed),
        updates_available=len(eligible) - len(installed),
        installed=[result.title for result in installed],
        failed=errors,
        reboot_required=outcome.reboot_required,
        result_code=outcome.result_code,
    )
    for result in installed:
        logger.log(SUCCESS, "Update installed: %s", result.title)
    for error in errors:
        logger.error("Update failed: %s", error)

    summary = f"{_plural(len(installed), 'atualização instalada', 'atualizações instaladas')}"
    if not errors:
        return ModuleResult(MODULE, Status.SUCCESS, summary, details)
    if installed:
        return ModuleResult(
            MODULE, Status.WARNING, f"{summary}, {len(errors)} com falha", details, errors=errors
        )
    return ModuleResult(
        MODULE,
        Status.FAILED,
        f"Falha ao instalar {_plural(len(errors), 'atualização', 'atualizações')} do Windows",
        details,
        errors=errors,
    )
