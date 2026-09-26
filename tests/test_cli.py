"""Testes da CLI com o ambiente simulado (nenhuma alteração no sistema real)."""

import json

import pytest

from maintenance import __version__, cli
from maintenance.runner import RunLock
from tests.fake_env import build_environment


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = build_environment(tmp_path, admin=False)
    monkeypatch.setattr(cli, "make_services", lambda: environment.services)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"data_dir": environment.config.data_dir}), encoding="utf-8")
    environment.config_file = config_file
    return environment


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_help_lists_all_commands(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for command in ("status", "check", "run", "report", "history", "version", "help"):
        assert command in out
    assert "códigos de saída" in out


def test_no_arguments_prints_help(capsys):
    code, out, _ = run_cli(capsys)
    assert code == 0 and "comandos" in out


def test_help_command_and_topics(capsys):
    assert run_cli(capsys, "help")[0] == 0
    code, out, _ = run_cli(capsys, "help", "run")
    assert code == 0 and "--dry-run" in out and "--windows-update" in out
    assert run_cli(capsys, "help", "desconhecido")[0] == 2


def test_version(capsys):
    code, out, _ = run_cli(capsys, "version")
    assert code == 0
    assert out.strip() == f"Windows Maintenance Agent v{__version__}"


def test_invalid_arguments_exit_with_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run", "--nao-existe"])
    assert exit_info.value.code == 2


def test_status(env, capsys):
    code, out, _ = run_cli(capsys, "status", "--config", str(env.config_file))
    assert code == 0
    for expected in (
        "Hostname: TI-04",
        "Usuário: tecnico",
        "Windows: Windows 11 Pro",
        "Versão: 24H2 (build 26100.4061)",
        "Arquitetura: x64",
        "Administrador: Não",
        "Winget:\nDisponível (v1.29.380)",
        "Chrome:\nInstalado\nVersão: 153.0.8010.53 (todos os usuários)",
        "Disco C:\nTotal: 256.0 GB\nLivre: 100.0 GB\nUso: 61%",
        "Última manutenção:\nNenhuma registrada",
        "Reinicialização pendente:\nNão",
        f"Agent:\nv{__version__}",
    ):
        assert expected in out, expected
    assert not env.paths.history_db.exists()  # status é somente leitura


def test_check(env, capsys):
    code, out, _ = run_cli(capsys, "check", "--config", str(env.config_file))
    assert code == 1
    assert "nenhuma alteração será feita" in out
    assert "[OK] Winget disponível (v1.29.380)" in out
    assert "[UPDATE] Google Chrome possui atualização (153.0.8010.53 -> 154.0.8037.58)" in out
    assert "[UPDATE] 1 atualização do Windows encontrada" in out
    assert "         - Cumulative (KB5065789)" in out
    assert "[OK] 4.0 KB de arquivos temporários removíveis" in out
    assert "[OK] Nenhuma reinicialização pendente" in out
    assert "Resumo:\nAtualizações disponíveis: 2\nEspaço recuperável estimado: 4.0 KB" in out
    assert env.old_file.exists()


def test_run_dry_run_then_history_and_report(env, capsys):
    code, out, _ = run_cli(capsys, "run", "--dry-run", "--config", str(env.config_file))
    assert code == 1
    assert "| INFO | Maintenance started" in out
    assert "WINDOWS MAINTENANCE REPORT" in out
    assert "Modo: DRY-RUN" in out
    assert "Relatório salvo em:" in out
    assert env.old_file.exists()

    code, out, _ = run_cli(capsys, "history", "--config", str(env.config_file))
    assert code == 0
    assert "#1 | 26/09/2026 08:00 | SUCCESS_WITH_WARNINGS | " in out
    assert "DRY-RUN" in out

    code, out, _ = run_cli(capsys, "report", "--config", str(env.config_file))
    assert code == 0 and "WINDOWS MAINTENANCE REPORT" in out
    code, out, _ = run_cli(capsys, "report", "--id", "1", "--config", str(env.config_file))
    assert code == 0 and "Execução: #1" in out
    code, out, _ = run_cli(capsys, "report", "--id", "99", "--config", str(env.config_file))
    assert code == 1 and "não encontrada" in out

    code, out, _ = run_cli(capsys, "status", "--config", str(env.config_file))
    assert "Última manutenção:\nNenhuma registrada" in out  # dry-run não conta


def test_run_selected_task_only(env, capsys):
    code, out, _ = run_cli(capsys, "run", "--cleanup", "--config", str(env.config_file))
    assert code == 1  # sem admin: WARNING de privilégios
    assert not env.old_file.exists()
    assert "Chrome\n[SKIPPED] Não solicitado nesta execução" in out
    assert "upgrade" not in env.runner.subcommands()


def test_history_and_report_when_empty(env, capsys):
    code, out, _ = run_cli(capsys, "history", "--config", str(env.config_file))
    assert code == 0 and "Nenhuma execução registrada." in out
    code, out, _ = run_cli(capsys, "report", "--config", str(env.config_file))
    assert code == 1 and "Nenhum relatório encontrado" in out


def test_invalid_config_is_reported(tmp_path, capsys):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"update_chrome": "sim"}', encoding="utf-8")
    code, _, err = run_cli(capsys, "status", "--config", str(config_file))
    assert code == 2
    assert "Erro de configuração" in err


def test_run_while_another_run_is_active(env, capsys):
    env.paths.ensure()
    lock = RunLock(env.paths.lock_file)
    assert lock.acquire()
    try:
        code, _, err = run_cli(capsys, "run", "--config", str(env.config_file))
    finally:
        lock.release()
    assert code == 2
    assert "Erro crítico: outra manutenção já está em execução" in err
