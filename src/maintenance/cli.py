"""Interface de linha de comando: ``maintenance <comando>``."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

from maintenance import APP_NAME, __version__
from maintenance.chrome import detect_chrome
from maintenance.config import AppPaths, ConfigError, LoadedConfig, load_config, resolve_paths
from maintenance.formatting import (
    format_bytes,
    format_datetime,
    format_duration,
    format_uptime,
)
from maintenance.history import open_history_readonly
from maintenance.reboot import detect_pending_reboot
from maintenance.reports import render_text
from maintenance.results import (
    TASK_CHROME,
    TASK_CLEANUP,
    TASK_WINDOWS_UPDATE,
    ModuleResult,
)
from maintenance.runner import (
    EXIT_FAILED,
    CriticalError,
    RunOptions,
    Services,
    ensure_supported_platform,
    run_check,
    run_maintenance,
)
from maintenance.winget import WingetInfo, get_winget_info

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INTERRUPTED = 130
MAX_LISTED_UPDATES = 10

EPILOG = """\
exemplos:
  maintenance status                   estado atual da máquina (rápido)
  maintenance check                    verifica pendências sem alterar nada
  maintenance run                      manutenção completa (conforme config.json)
  maintenance run --chrome --cleanup   apenas as tarefas escolhidas
  maintenance run --dry-run            simulação completa, sem alterações
  maintenance history --limit 20       últimas execuções
  maintenance report --id 12           relatório de uma execução

códigos de saída: 0 = sucesso, 1 = sucesso com pendências, 2 = falha, 130 = interrompido
"""


def make_services() -> Services:
    """Ponto único de criação das dependências reais (substituído nos testes)."""
    return Services()


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=argparse.SUPPRESS,
        help="arquivo de configuração (padrão: config.json na pasta do agente)",
    )
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="inclui mensagens de depuração no log",
    )

    parser = argparse.ArgumentParser(
        prog="maintenance",
        description=f"{APP_NAME} v{__version__} - manutenção preventiva de estações Windows.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help=argparse.SUPPRESS
    )
    commands = parser.add_subparsers(dest="command", metavar="<comando>", title="comandos")

    commands.add_parser(
        "status", parents=[common], help="estado atual da máquina (rápido, somente leitura)"
    )
    check = commands.add_parser(
        "check",
        parents=[common],
        help="verifica atualizações, disco, temporários e reinicialização (não altera nada)",
    )
    run = commands.add_parser(
        "run",
        parents=[common],
        help="executa a manutenção (completa ou apenas as tarefas escolhidas)",
        description="Sem flags de tarefa, executa as tarefas habilitadas no config.json. "
        "Com flags, executa apenas as tarefas indicadas.",
    )
    for subparser in (check, run):
        subparser.add_argument("--chrome", action="store_true", help="Google Chrome (via winget)")
        subparser.add_argument(
            "--windows-update", action="store_true", help="atualizações do Windows"
        )
        subparser.add_argument("--cleanup", action="store_true", help="arquivos temporários")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="simula todo o fluxo sem alterar o computador (gera log, relatório e histórico)",
    )
    report = commands.add_parser(
        "report", parents=[common], help="exibe o relatório da última manutenção"
    )
    report.add_argument("--id", type=int, dest="run_id", help="ID da execução (ver 'history')")
    history = commands.add_parser("history", parents=[common], help="lista as últimas execuções")
    history.add_argument("--limit", type=int, default=10, help="quantidade (padrão: 10)")
    commands.add_parser("version", help="exibe a versão do agente")
    help_parser = commands.add_parser("help", help="exibe esta ajuda ou a ajuda de um comando")
    help_parser.add_argument("topic", nargs="?", help="comando")
    return parser


def _selected_tasks(args: argparse.Namespace) -> tuple[str, ...]:
    flags = {
        TASK_CHROME: args.chrome,
        TASK_WINDOWS_UPDATE: args.windows_update,
        TASK_CLEANUP: args.cleanup,
    }
    return tuple(task for task, enabled in flags.items() if enabled)


def _load(args: argparse.Namespace) -> tuple[LoadedConfig, AppPaths]:
    loaded = load_config(args.config)
    return loaded, resolve_paths(loaded.config)


def _print_notes(loaded: LoadedConfig) -> None:
    for note in loaded.notes:
        print(f"Aviso: {note}", file=sys.stderr)


def _winget_label(winget: WingetInfo | None) -> str:
    if winget is None:
        return "Não encontrado"
    return f"Disponível (v{winget.version})" if winget.version else "Disponível"


def cmd_status(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    ensure_supported_platform()
    loaded, paths = _load(args)
    _print_notes(loaded)
    services = make_services()
    info = services.collect_system_info(services.registry)
    privileges = services.get_privileges()
    winget = get_winget_info(services.find_winget(), runner=services.run_command)
    chrome = detect_chrome(services.registry)
    reboot = detect_pending_reboot(services.registry)
    history = open_history_readonly(paths.history_db)
    last = history.last_completed_run() if history else None

    lines = [
        APP_NAME,
        "",
        f"Hostname: {info.hostname}",
        f"Usuário: {info.username}",
        f"Windows: {info.windows.product_name}",
        f"Versão: {info.windows.display_version} (build {info.windows.build})",
        f"Arquitetura: {info.architecture}",
        f"Administrador: {privileges.label()}",
        f"Tempo ligado: {format_uptime(info.uptime_seconds)}",
        "",
        "Winget:",
        _winget_label(winget),
        "",
        "Chrome:",
    ]
    if chrome.installed:
        scope = "todos os usuários" if chrome.scope == "machine" else "usuário atual"
        lines += ["Instalado", f"Versão: {chrome.version or 'desconhecida'} ({scope})"]
    else:
        lines.append("Não instalado")
    lines.append("")
    if info.disk:
        lines += [
            f"Disco {info.disk.drive}",
            f"Total: {format_bytes(info.disk.total_bytes)}",
            f"Livre: {format_bytes(info.disk.free_bytes)}",
            f"Uso: {info.disk.used_percent:.0f}%",
        ]
    else:
        lines += ["Disco:", "Não foi possível obter o uso do disco"]
    lines += ["", "Última manutenção:"]
    lines.append(
        f"{format_datetime(last.started_at)} ({last.result})" if last else "Nenhuma registrada"
    )
    lines += ["", "Reinicialização pendente:"]
    lines.append(f"Sim ({', '.join(reboot.sources)})" if reboot.required else "Não")
    lines += ["", "Agent:", f"v{__version__}"]
    print("\n".join(lines))
    return EXIT_OK


def _check_line(result: ModuleResult) -> list[str]:
    lines = [f"[{result.tag}] {result.summary}"]
    if result.module == TASK_WINDOWS_UPDATE:
        pending = [u["title"] for u in result.details.get("updates", []) if u.get("eligible")]
        lines += [f"         - {title}" for title in pending[:MAX_LISTED_UPDATES]]
        if len(pending) > MAX_LISTED_UPDATES:
            lines.append(f"         ... e mais {len(pending) - MAX_LISTED_UPDATES}")
    for error in result.errors:
        lines.append(f"         ! {error}")
    return lines


def cmd_check(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    ensure_supported_platform()
    loaded, paths = _load(args)
    _print_notes(loaded)
    print(f"{APP_NAME} - verificação (nenhuma alteração será feita)\n", flush=True)

    def show(result: ModuleResult) -> None:
        print("\n".join(_check_line(result)), flush=True)

    def progress(message: str) -> None:
        print(message, flush=True)

    outcome = run_check(
        loaded.config,
        paths,
        tasks=_selected_tasks(args),
        verbose=args.verbose,
        services=make_services(),
        on_result=show,
        progress=progress,
    )
    recoverable = outcome.recoverable_bytes
    print("\nResumo:")
    print(f"Atualizações disponíveis: {outcome.updates_available}")
    if recoverable is not None:
        print(f"Espaço recuperável estimado: {format_bytes(recoverable)}")
    if outcome.log_file:
        print(f"\nLog: {outcome.log_file}")
    return outcome.exit_code


def cmd_run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    ensure_supported_platform()
    loaded, paths = _load(args)
    _print_notes(loaded)
    options = RunOptions(
        tasks=_selected_tasks(args), dry_run=args.dry_run, verbose=args.verbose, console=True
    )
    outcome = run_maintenance(loaded.config, paths, options, make_services())
    print()
    print(render_text(outcome.report))
    if outcome.report_file:
        print(f"Relatório salvo em: {outcome.report_file}")
    return outcome.exit_code


def cmd_report(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    loaded, paths = _load(args)
    history = open_history_readonly(paths.history_db)
    report_file: Path | None = None
    if args.run_id is not None:
        entry = history.get_run(args.run_id) if history else None
        if entry is None:
            print(f"Execução #{args.run_id} não encontrada no histórico.")
            return EXIT_NOT_FOUND
        report_file = Path(entry.report_file) if entry.report_file else None
    else:
        candidates = sorted(paths.reports_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
        report_file = candidates[-1] if candidates else None
    if report_file is None or not report_file.is_file():
        print("Nenhum relatório encontrado (os relatórios são gerados pelo comando 'run').")
        return EXIT_NOT_FOUND
    print(report_file.read_text(encoding="utf-8"))
    print(f"Arquivo: {report_file}")
    return EXIT_OK


def cmd_history(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    loaded, paths = _load(args)
    history = open_history_readonly(paths.history_db)
    entries = history.list_runs(max(args.limit, 1)) if history else []
    if not entries:
        print("Nenhuma execução registrada.")
        return EXIT_OK
    count = "1 execução" if len(entries) == 1 else f"últimas {len(entries)} execuções"
    print(f"Histórico de manutenção ({count})\n")
    for entry in entries:
        columns = [
            f"#{entry.id}",
            format_datetime(entry.started_at),
            entry.result,
            format_duration(entry.duration_seconds, compact=True),
        ]
        if entry.reboot_required:
            columns.append("reboot pendente")
        if entry.dry_run:
            columns.append("DRY-RUN")
        print(" | ".join(columns))
    return EXIT_OK


def cmd_version(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    print(f"{APP_NAME} v{__version__}")
    return EXIT_OK


def cmd_help(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.topic:
        subparsers = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        subparser = subparsers.choices.get(args.topic)
        if subparser is None:
            print(f"Comando desconhecido: {args.topic}\n")
            parser.print_help()
            return EXIT_FAILED
        subparser.print_help()
        return EXIT_OK
    parser.print_help()
    return EXIT_OK


COMMANDS: dict[str, Callable[[argparse.Namespace, argparse.ArgumentParser], int]] = {
    "status": cmd_status,
    "check": cmd_check,
    "run": cmd_run,
    "report": cmd_report,
    "history": cmd_history,
    "version": cmd_version,
    "help": cmd_help,
}


def _configure_console() -> None:
    # Saída sempre em UTF-8, como logs e relatórios. No console o Python já usa a API
    # Unicode do Windows; isto padroniza a saída redirecionada (arquivo, RMM, agendador),
    # que de outra forma dependeria da code page local (e o executável do PyInstaller
    # ignora PYTHONIOENCODING).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_OK
    try:
        return COMMANDS[args.command](args, parser)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
    except CriticalError as exc:
        print(f"Erro crítico: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nOperação interrompida pelo usuário.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:
        print(f"Erro inesperado: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        else:
            print("Execute novamente com -v para detalhes.", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
