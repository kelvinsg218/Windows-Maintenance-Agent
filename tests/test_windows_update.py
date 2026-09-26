"""Testes do módulo Windows Update com objetos COM falsos (nenhuma chamada real à WUA)."""

import time

import pytest

from maintenance.results import Status
from maintenance.windows_update import (
    ORC_FAILED,
    ORC_SUCCEEDED,
    SEARCH_CRITERIA,
    UPGRADES_CATEGORY_ID,
    OperationOutcome,
    UpdateItem,
    UpdateOperationResult,
    WindowsUpdateError,
    classify,
    describe_error,
    hresult_from_exception,
    process_windows_update,
    update_item_from_com,
)


class FakeCollection:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):  # noqa: N802 - nomes da API COM
        return len(self._items)

    def Item(self, index):  # noqa: N802
        return self._items[index]


class FakeCategory:
    def __init__(self, name, category_id):
        self.Name = name
        self.CategoryID = category_id


class FakeBehavior:
    def __init__(self, user_input=False, reboot=1):
        self.CanRequestUserInput = user_input
        self.RebootBehavior = reboot


class FakeIdentity:
    def __init__(self, update_id):
        self.UpdateID = update_id


class FakeUpdate:
    def __init__(
        self,
        title,
        kb=("5065789",),
        *,
        optional=False,
        downloaded=False,
        eula=True,
        user_input=False,
        feature=False,
    ):
        categories = [FakeCategory("Security Updates", "0fa1201d-4330-4fa8-8ae9-b877473b6441")]
        if feature:
            categories.append(FakeCategory("Upgrades", UPGRADES_CATEGORY_ID.upper()))
        self.Title = title
        self.Identity = FakeIdentity(f"id-{title}")
        self.KBArticleIDs = FakeCollection(kb)
        self.Categories = FakeCollection(categories)
        self.MaxDownloadSize = 1024
        self.MsrcSeverity = None
        self.BrowseOnly = optional
        self.IsDownloaded = downloaded
        self.EulaAccepted = eula
        self.InstallationBehavior = FakeBehavior(user_input)

    def AcceptEula(self):  # noqa: N802
        self.EulaAccepted = True


class FakeComError(Exception):
    """Imita pywintypes.com_error: (hresult, texto, excepinfo, argerror)."""


class FakeClient:
    def __init__(
        self,
        updates,
        *,
        search_error=None,
        search_delay=0.0,
        busy=False,
        reboot_before=False,
        download_fail=(),
        install_fail=(),
        reboot_after=False,
    ):
        self.updates = updates
        self.search_error = search_error
        self.search_delay = search_delay
        self.busy = busy
        self.reboot_before = reboot_before
        self.download_fail = set(download_fail)
        self.install_fail = set(install_fail)
        self.reboot_after = reboot_after
        self.criteria = None
        self.downloaded = []
        self.installed = []
        self.accepted = []

    def search(self, criteria):
        self.criteria = criteria
        time.sleep(self.search_delay)
        if self.search_error:
            raise self.search_error
        return list(self.updates)

    def installer_busy(self):
        return self.busy

    def reboot_required_before_installation(self):
        return self.reboot_before

    def accept_eula(self, update):
        self.accepted.append(update.Title)
        update.AcceptEula()

    def download(self, updates):
        results = []
        for update in updates:
            self.downloaded.append(update.Title)
            ok = update.Title not in self.download_fail
            update.IsDownloaded = ok
            code = ORC_SUCCEEDED if ok else ORC_FAILED
            results.append(UpdateOperationResult(update.Title, code, 0 if ok else 0x80072EE2))
        return OperationOutcome(ORC_SUCCEEDED, False, tuple(results))

    def install(self, updates):
        results = []
        for update in updates:
            self.installed.append(update.Title)
            failed = update.Title in self.install_fail
            code = ORC_FAILED if failed else ORC_SUCCEEDED
            results.append(UpdateOperationResult(update.Title, code, 0x80070643 if failed else 0))
        return OperationOutcome(ORC_SUCCEEDED, self.reboot_after, tuple(results))


def run(client, **kwargs):
    options = {"apply": False, "is_admin": False, "search_timeout": 10, "install_timeout": 10}
    options.update(kwargs)
    return process_windows_update(client_factory=lambda: client, **options)


def mixed_updates():
    return [
        FakeUpdate("Cumulative Update (KB5065789)"),
        FakeUpdate("Defender Definitions", kb=("2267602",), downloaded=True),
        FakeUpdate("Optional Preview", optional=True),
        FakeUpdate("Windows 11, version 26H1", kb=(), feature=True),
        FakeUpdate("Needs EULA", eula=False),
        FakeUpdate("Interactive", user_input=True),
    ]


def test_update_item_is_built_from_com_object():
    item = update_item_from_com(FakeUpdate("Update X", kb=("123", "456"), feature=True))
    assert item.title == "Update X"
    assert item.kb_articles == ["KB123", "KB456"]
    assert item.feature_upgrade is True
    assert item.size_bytes == 1024
    assert item.severity == ""
    assert item.update_id == "id-Update X"


def test_update_item_tolerates_missing_com_properties():
    class Broken:
        Title = "Parcial"

    item = update_item_from_com(Broken())
    assert item.title == "Parcial"
    assert item.kb_articles == [] and item.eula_accepted is True


def test_classification_rules():
    items = [update_item_from_com(update) for update in mixed_updates()]
    classify(items, include_optional=False, accept_eulas=False)
    eligible = {item.title for item in items if item.eligible}
    assert eligible == {"Cumulative Update (KB5065789)", "Defender Definitions"}
    reasons = {item.title: (item.skip_reason, item.manual_action) for item in items}
    assert reasons["Optional Preview"] == ("atualização opcional", False)
    assert reasons["Windows 11, version 26H1"][1] is False
    assert reasons["Needs EULA"][1] is True
    assert reasons["Interactive"][1] is True

    classify(items, include_optional=True, accept_eulas=True)
    assert {item.title for item in items if item.eligible} >= {"Optional Preview", "Needs EULA"}
    assert not next(i for i in items if i.title == "Windows 11, version 26H1").eligible


def test_check_only_searches_and_reports_updates():
    client = FakeClient(mixed_updates())
    result = run(client, apply=False)
    assert client.criteria == SEARCH_CRITERIA
    assert result.status is Status.WARNING and result.tag == "UPDATE"
    assert result.summary.startswith("2 atualizações do Windows encontradas")
    assert "2 exigem ação manual" in result.summary
    assert result.details["updates_available"] == 2
    assert client.downloaded == [] and client.installed == []


def test_no_updates_is_success():
    result = run(FakeClient([FakeUpdate("Optional", optional=True)]))
    assert result.status is Status.SUCCESS
    assert "Nenhuma atualização do Windows pendente" in result.summary
    assert "1 opcionais" in result.summary


def test_without_admin_nothing_is_installed():
    client = FakeClient(mixed_updates())
    result = run(client, apply=True, is_admin=False)
    assert result.status is Status.WARNING
    assert "requer privilégios administrativos" in result.summary
    assert client.installed == []


def test_dry_run_nothing_is_installed():
    client = FakeClient([FakeUpdate("Cumulative")])
    result = run(client, apply=True, is_admin=True, dry_run=True)
    assert result.status is Status.WARNING
    assert "1 atualização pendente; não instalada (dry-run)" == result.summary
    assert client.installed == []


def test_admin_installs_only_eligible_updates_and_reports_reboot():
    client = FakeClient(mixed_updates(), reboot_after=True)
    result = run(client, apply=True, is_admin=True)
    assert result.status is Status.SUCCESS
    assert result.summary == "2 atualizações instaladas"
    assert client.downloaded == ["Cumulative Update (KB5065789)"]  # a outra já estava baixada
    assert set(client.installed) == {"Cumulative Update (KB5065789)", "Defender Definitions"}
    assert result.details["updates_installed"] == 2
    assert result.details["reboot_required"] is True
    assert client.accepted == []


def test_eulas_are_accepted_only_when_configured():
    client = FakeClient([FakeUpdate("Needs EULA", eula=False)])
    result = run(client, apply=True, is_admin=True, accept_eulas=True)
    assert result.status is Status.SUCCESS
    assert client.accepted == ["Needs EULA"]


def test_partial_and_total_install_failures():
    updates = [FakeUpdate("A"), FakeUpdate("B")]
    partial = run(FakeClient(updates, install_fail={"B"}), apply=True, is_admin=True)
    assert partial.status is Status.WARNING
    assert partial.summary == "1 atualização instalada, 1 com falha"
    assert "0x80070643" in partial.errors[0]

    updates = [FakeUpdate("A"), FakeUpdate("B")]
    total = run(FakeClient(updates, install_fail={"A", "B"}), apply=True, is_admin=True)
    assert total.status is Status.FAILED
    assert "erro fatal durante a instalação" in total.errors[0]


def test_failed_download_is_reported():
    client = FakeClient([FakeUpdate("A"), FakeUpdate("B")], download_fail={"B"})
    result = run(client, apply=True, is_admin=True)
    assert result.status is Status.WARNING
    assert client.installed == ["A"]
    assert any("download não concluído" in error for error in result.errors)

    client = FakeClient([FakeUpdate("A")], download_fail={"A"})
    result = run(client, apply=True, is_admin=True)
    assert result.status is Status.FAILED
    assert client.installed == []


@pytest.mark.parametrize(
    ("client_options", "text"),
    [
        ({"reboot_before": True}, "reinicialização pendente"),
        ({"busy": True}, "outra instalação"),
    ],
)
def test_installation_blocked(client_options, text):
    client = FakeClient([FakeUpdate("A")], **client_options)
    result = run(client, apply=True, is_admin=True)
    assert result.status is Status.WARNING
    assert text in result.summary
    assert client.installed == []


def test_search_com_error_is_translated():
    excepinfo = (0, "WUA", "erro", None, 0, 0x8024402C - 2**32)
    error = FakeComError(-2147352567, "Exception occurred.", excepinfo, None)
    result = run(FakeClient([], search_error=error))
    assert result.status is Status.FAILED
    assert "0x8024402C" in result.summary
    assert "verifique rede/proxy" in result.summary


def test_search_timeout_does_not_block_the_agent():
    started = time.monotonic()
    result = run(FakeClient([], search_delay=3), search_timeout=0.3)
    assert result.status is Status.FAILED
    assert "Tempo limite" in result.summary
    assert time.monotonic() - started < 2.5


def test_missing_pywin32_is_reported():
    def factory():
        raise ImportError("No module named 'win32com'")

    result = process_windows_update(apply=False, is_admin=False, client_factory=factory)
    assert result.status is Status.FAILED
    assert "pywin32" in result.summary


def test_error_helpers():
    assert hresult_from_exception(FakeComError(-2147024891, "Acesso negado", None, None)) == (
        0x80070005
    )
    assert hresult_from_exception(ValueError("x")) is None
    assert describe_error(WindowsUpdateError("falhou", 0x80070422)).endswith("desabilitado)")
    assert describe_error(RuntimeError("texto")) == "texto"


def test_update_item_serialization():
    item = UpdateItem(update_id="1", title="T")
    assert item.to_dict()["title"] == "T"
