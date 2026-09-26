"""Leitura (somente leitura) do Registro do Windows, sempre na visão de 64 bits."""

from __future__ import annotations

from typing import Any

HKLM = "HKLM"
HKCU = "HKCU"


class RegistryReader:
    """Interface mínima de leitura do Registro, substituível por fakes nos testes.

    Todas as operações retornam ``None``/``False``/``[]`` quando a chave ou o valor
    não existem ou não podem ser lidos; nunca lançam exceção.
    """

    def get_value(self, hive: str, key: str, name: str) -> Any | None:
        raise NotImplementedError

    def key_exists(self, hive: str, key: str) -> bool:
        raise NotImplementedError

    def list_subkeys(self, hive: str, key: str) -> list[str]:
        raise NotImplementedError


class WinRegistryReader(RegistryReader):
    """Implementação real baseada em ``winreg`` (stdlib)."""

    def __init__(self) -> None:
        import winreg

        self._winreg = winreg
        self._hives = {HKLM: winreg.HKEY_LOCAL_MACHINE, HKCU: winreg.HKEY_CURRENT_USER}
        # KEY_WOW64_64KEY garante a visão de 64 bits mesmo em um Python de 32 bits.
        self._access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY

    def _open(self, hive: str, key: str) -> Any:
        return self._winreg.OpenKey(self._hives[hive], key, 0, self._access)

    def get_value(self, hive: str, key: str, name: str) -> Any | None:
        try:
            with self._open(hive, key) as handle:
                value, _value_type = self._winreg.QueryValueEx(handle, name)
                return value
        except OSError:
            return None

    def key_exists(self, hive: str, key: str) -> bool:
        try:
            with self._open(hive, key):
                return True
        except OSError:
            return False

    def list_subkeys(self, hive: str, key: str) -> list[str]:
        names: list[str] = []
        try:
            with self._open(hive, key) as handle:
                index = 0
                while True:
                    try:
                        names.append(self._winreg.EnumKey(handle, index))
                    except OSError:
                        break
                    index += 1
        except OSError:
            return []
        return names


class NullRegistryReader(RegistryReader):
    """Usado fora do Windows: nenhuma chave existe."""

    def get_value(self, hive: str, key: str, name: str) -> Any | None:
        return None

    def key_exists(self, hive: str, key: str) -> bool:
        return False

    def list_subkeys(self, hive: str, key: str) -> list[str]:
        return []


def default_registry() -> RegistryReader:
    try:
        return WinRegistryReader()
    except ImportError:
        return NullRegistryReader()
