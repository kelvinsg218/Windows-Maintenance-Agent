import json
from pathlib import Path

import pytest

from maintenance.config import (
    CLEANUP_TARGET_NAMES,
    AppConfig,
    AppPaths,
    ConfigError,
    load_config,
    parse_config,
    resolve_paths,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_config(tmp_path: Path, data) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_defaults_when_default_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("maintenance.config.default_config_path", lambda: tmp_path / "none.json")
    loaded = load_config()
    assert loaded.config == AppConfig()
    assert loaded.path is None
    assert "usando configuração padrão" in loaded.notes[0]


def test_explicit_missing_file_is_error(tmp_path):
    with pytest.raises(ConfigError, match="não encontrado"):
        load_config(tmp_path / "missing.json")


def test_valid_file_is_loaded(tmp_path):
    path = write_config(
        tmp_path,
        {
            "_comment": "ignorado",
            "update_chrome": False,
            "log_retention_days": 30,
            "network_log_path": "\\\\SERVIDOR\\TI\\maintenance\\",
            "cleanup_targets": ["user_temp", "user_temp"],
        },
    )
    loaded = load_config(path)
    assert loaded.config.update_chrome is False
    assert loaded.config.log_retention_days == 30
    assert loaded.config.network_log_path == "\\\\SERVIDOR\\TI\\maintenance"
    assert loaded.config.cleanup_targets == ("user_temp",)
    assert loaded.notes == []


def test_file_with_utf8_bom_is_accepted(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"update_windows": false}', encoding="utf-8-sig")
    assert load_config(path).config.update_windows is False


def test_invalid_json_reports_position(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"update_chrome": tru}', encoding="utf-8")
    with pytest.raises(ConfigError, match="linha 1"):
        load_config(path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "objeto JSON"),
        ({"update_chrome": "yes"}, "true ou false"),
        ({"log_retention_days": True}, "inteiro"),
        ({"log_retention_days": -1}, "entre"),
        ({"cleanup_min_age_days": 0}, "entre"),
        ({"cleanup_targets": ["C:\\Windows"]}, "desconhecidos"),
        ({"cleanup_targets": "user_temp"}, "lista"),
        ({"network_log_path": "servidor/share"}, "caminho absoluto"),
        ({"network_log_path": "\\\\SERVIDOR\\TI\\..\\outro"}, "caminho absoluto"),
        ({"network_log_path": "http://servidor/logs"}, "caminho absoluto"),
        ({"network_log_path": "\\\\usuario:senha@SERVIDOR\\TI"}, "caminho absoluto"),
        ({"data_dir": "relativo\\pasta"}, "caminho local absoluto"),
    ],
)
def test_invalid_values_are_rejected(data, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


@pytest.mark.parametrize("key", ["password", "share_password", "api_key", "token", "senha"])
def test_credential_like_keys_are_rejected(key):
    with pytest.raises(ConfigError, match="credenciais"):
        parse_config({key: "x"})


def test_unknown_keys_are_reported_not_fatal():
    config, notes = parse_config({"unknown_option": 1})
    assert config == AppConfig()
    assert "unknown_option" in notes[0]


def test_example_config_matches_defaults():
    """O config.example.json documentado deve ser válido e refletir os padrões do código."""
    config, notes = parse_config(
        json.loads((PROJECT_ROOT / "config.example.json").read_text(encoding="utf-8"))
    )
    assert notes == []
    assert config == AppConfig()


def test_paths_from_data_dir(tmp_path):
    config, _ = parse_config({"data_dir": str(tmp_path)})
    paths = resolve_paths(config)
    assert paths == AppPaths.from_base(tmp_path)
    assert paths.history_db == tmp_path / "data" / "history.db"
    paths.ensure()
    assert paths.logs_dir.is_dir() and paths.reports_dir.is_dir() and paths.data_dir.is_dir()


def test_default_cleanup_targets_are_all_known():
    assert AppConfig().cleanup_targets == CLEANUP_TARGET_NAMES
