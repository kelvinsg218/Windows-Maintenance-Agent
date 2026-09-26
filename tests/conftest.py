"""Utilidades compartilhadas pelos testes."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from maintenance.registry import RegistryReader

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="requer Windows")


class FakeRegistry(RegistryReader):
    """Registro em memória. Chaves e nomes de valores são case-insensitive, como no Windows."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], Any] = {}
        self._keys: set[tuple[str, str]] = set()

    def set(self, hive: str, key: str, name: str, value: Any) -> FakeRegistry:
        self._values[(hive.upper(), key.lower(), name.lower())] = value
        self.add_key(hive, key)
        return self

    def add_key(self, hive: str, key: str) -> FakeRegistry:
        parts = key.split("\\")
        for index in range(1, len(parts) + 1):
            self._keys.add((hive.upper(), "\\".join(parts[:index]).lower()))
        return self

    def get_value(self, hive: str, key: str, name: str) -> Any | None:
        return self._values.get((hive.upper(), key.lower(), name.lower()))

    def key_exists(self, hive: str, key: str) -> bool:
        return (hive.upper(), key.lower()) in self._keys

    def list_subkeys(self, hive: str, key: str) -> list[str]:
        prefix = key.lower() + "\\"
        names = {
            k[len(prefix) :].split("\\")[0]
            for h, k in self._keys
            if h == hive.upper() and k.startswith(prefix)
        }
        return sorted(names)


@pytest.fixture
def registry() -> FakeRegistry:
    return FakeRegistry()
