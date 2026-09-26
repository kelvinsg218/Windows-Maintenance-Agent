"""Validações para a futura atualização segura do próprio agente.

Nada neste módulo baixa ou executa código. A atualização do agente será feita por
releases versionadas, nunca por scripts remotos:

1. ler um manifesto de release publicado em local conhecido (HTTPS, host autorizado);
2. comparar a versão disponível com a instalada;
3. baixar o artefato para uma área temporária;
4. verificar o SHA-256 (e, planejado, a assinatura Authenticode);
5. validar o novo executável (ex.: ``maintenance.exe version``);
6. substituir mantendo backup da versão anterior e fazer rollback em caso de falha.

Este módulo concentra as verificações puras (1, 2 e 4), já testadas.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ReleaseError(ValueError):
    """Manifesto de release inválido ou artefato não confiável."""


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    url: str
    sha256: str


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION.match(text.strip())
    if not match:
        raise ReleaseError(f"versão inválida: {text!r} (esperado: MAJOR.MINOR.PATCH)")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def validate_manifest(
    data: Mapping[str, Any], *, allowed_hosts: Collection[str]
) -> ReleaseManifest:
    """Valida um manifesto ``{"version", "url", "sha256"}`` antes de qualquer download."""
    version = str(data.get("version", "")).strip()
    url = str(data.get("url", "")).strip()
    sha256 = str(data.get("sha256", "")).strip()
    parse_version(version)
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ReleaseError("o artefato deve ser obtido via HTTPS")
    if parsed.username or parsed.password:
        raise ReleaseError("a URL do artefato não pode conter credenciais")
    hosts = {host.lower() for host in allowed_hosts}
    if not parsed.hostname or parsed.hostname.lower() not in hosts:
        raise ReleaseError(f"host não autorizado para releases: {parsed.hostname!r}")
    if not _SHA256.match(sha256):
        raise ReleaseError("SHA-256 ausente ou inválido no manifesto")
    return ReleaseManifest(version, url, sha256.lower())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    """Compara o hash do arquivo com o esperado (comparação em tempo constante)."""
    if not _SHA256.match(expected.strip()):
        raise ReleaseError("SHA-256 esperado inválido")
    return hmac.compare_digest(sha256_file(path), expected.strip().lower())
