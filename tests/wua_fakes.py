"""Objetos falsos que imitam a Windows Update Agent API (COM) nos testes."""

import time

from maintenance.windows_update import (
    ORC_FAILED,
    ORC_SUCCEEDED,
    UPGRADES_CATEGORY_ID,
    OperationOutcome,
    UpdateOperationResult,
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
