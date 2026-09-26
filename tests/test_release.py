import hashlib

import pytest

from maintenance import __version__
from maintenance.release import (
    ReleaseError,
    is_newer,
    parse_version,
    validate_manifest,
    verify_sha256,
)

HOSTS = ["releases.empresa.local"]
DIGEST = "a" * 64


def manifest(**overrides):
    data = {
        "version": "1.1.0",
        "url": "https://releases.empresa.local/wma/1.1.0/maintenance.exe",
        "sha256": DIGEST,
    }
    data.update(overrides)
    return data


def test_current_version_is_valid_semver():
    assert parse_version(__version__) >= (1, 0, 0)


def test_version_comparison():
    assert parse_version("v2.10.3") == (2, 10, 3)
    assert is_newer("1.10.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("0.9.0", "1.0.0")
    with pytest.raises(ReleaseError):
        parse_version("1.0")
    with pytest.raises(ReleaseError):
        parse_version("1.0.0; rm -rf")


def test_valid_manifest():
    release = validate_manifest(manifest(sha256=DIGEST.upper()), allowed_hosts=HOSTS)
    assert release.version == "1.1.0"
    assert release.sha256 == DIGEST


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"url": "http://releases.empresa.local/wma.exe"}, "HTTPS"),
        ({"url": "https://evil.example.com/wma.exe"}, "host não autorizado"),
        ({"url": "https://user:pass@releases.empresa.local/wma.exe"}, "credenciais"),
        ({"sha256": "abc"}, "SHA-256"),
        ({"sha256": ""}, "SHA-256"),
        ({"version": "latest"}, "versão inválida"),
    ],
)
def test_invalid_manifests_are_rejected(overrides, message):
    with pytest.raises(ReleaseError, match=message):
        validate_manifest(manifest(**overrides), allowed_hosts=HOSTS)


def test_sha256_verification(tmp_path):
    artifact = tmp_path / "maintenance.exe"
    artifact.write_bytes(b"conteudo do executavel")
    expected = hashlib.sha256(b"conteudo do executavel").hexdigest()
    assert verify_sha256(artifact, expected)
    assert verify_sha256(artifact, expected.upper())
    artifact.write_bytes(b"conteudo adulterado")
    assert not verify_sha256(artifact, expected)
    with pytest.raises(ReleaseError):
        verify_sha256(artifact, "nao-e-hash")
