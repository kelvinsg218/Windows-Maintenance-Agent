"""Orquestração dos fluxos ``check`` (somente leitura) e ``run`` (manutenção).

Cada módulo roda isolado: uma falha vira um resultado FAILED no relatório e a
manutenção segue para o próximo módulo. Apenas erros críticos (plataforma não
suportada, impossibilidade de gravar logs, outra execução em andamento) abortam.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO

from maintenance import __version__, chrome, cleanup, windows_update
from maintenance.config import AppConfig, AppPaths
from maintenance.formatting import format_bytes, format_duration
from maintenance.history import HistoryStore
from maintenance.logging_service import (
    SUCCESS,
    close_logging,
    configure_logging,
    flush_logging,
    purge_old_files,
    run_file_stem,
)
from maintenance.network_sync import sync_files
from maintenance.privileges import PrivilegeInfo, get_privilege_info, privilege_result
from maintenance.process import CommandRunner, run_command
from maintenance.reboot import RebootStatus, detect_pending_reboot, reboot_result
from maintenance.registry import RegistryReader, default_registry
from maintenance.reports import MaintenanceReport, write_report
from maintenance.results import (
    EXIT_CODES,
    TASK_CHROME,
    TASK_CLEANUP,
    TASK_WINDOWS_UPDATE,
    TASKS,
    ModuleResult,
    OverallResult,
    Status,
    aggregate,
)
from maintenance.system_info import (
    DiskUsage,
    SystemInfo,
    collect_system_info,
    evaluate_disk_space,
    get_disk_usage,
    get_system_drive,
)
from maintenance.winget import WingetInfo, find_winget, get_winget_info, winget_result

logger = logging.getLogger("maintenance")

EXIT_FAILED = EXIT_CODES[OverallResult.FAILED]


class CriticalError(Exception):
    """Erro que impede a manutenção de começar."""


def _local_now() -> datetime:
    return datetime.now().astimezone()


@dataclass
class Services:
    """Dependências externas do agente, substituíveis nos testes."""

    registry: RegistryReader = field(default_factory=default_registry)
    get_privileges: Callable[[], PrivilegeInfo] = get_privilege_info
    collect_system_info: Callable[[RegistryReader], SystemInfo] = collect_system_info
    find_winget: Callable[[], Path | None] = find_winget
    run_command: CommandRunner = run_command
    wua_client_factory: Callable[[], windows_update.WuaClient] = windows_update.WuaComClient
    disk_usage: Callable[[str], DiskUsage | None] = get_disk_usage
    system_drive: Callable[[], str] = get_system_drive
    now: Callable[[], datetime] = _local_now
    cleanup_env: Mapping[str, str] | None = None  # None = ambiente real
    windows_dir: Path | None = None  # None = pasta do Windows obtida pela API


@dataclass(frozen=True)
class RunOptions:
    tasks: tuple[str, ...] = ()  # vazio = tarefas habilitadas na configuração
    dry_run: bool = False
    verbose: bool = False
    console: bool = True


@dataclass
class RunOutcome:
    overall: OverallResult
    report: MaintenanceReport
    log_file: Path
    report_file: Path | None
    json_file: Path | None
    run_id: int | None
    network_sync: ModuleResult

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.overall]


@dataclass
class CheckOutcome:
    info: SystemInfo
    privileges: PrivilegeInfo
    results: list[ModuleResult]
    log_file: Path | None

    @property
    def updates_available(self) -> int:
        return sum(result.updates_available for result in self.results)

    @property
    def recoverable_bytes(self) -> int | None:
        result = next((r for r in self.results if r.module == TASK_CLEANUP), None)
        return result.details.get("bytes_eligible") if result else None

    @property
    def exit_code(self) -> int:
        statuses = {result.status for result in self.results}
        if Status.FAILED in statuses:
            return 2
        return 1 if Status.WARNING in statuses else 0


def ensure_supported_platform() -> None:
    if sys.platform != "win32":
        raise CriticalError("o Windows Maintenance Agent funciona apenas no Windows 10/11")


def select_tasks(config: AppConfig, requested: Sequence[str] = ()) -> list[str]:
    """Flags explícitas da CLI têm prioridade; sem flags, valem os toggles da configuração."""
    if requested:
        return [task for task in TASKS if task in requested]
    enabled = {
        TASK_CHROME: config.update_chrome,
        TASK_WINDOWS_UPDATE: config.update_windows,
        TASK_CLEANUP: config.cleanup_temp,
    }
    return [task for task in TASKS if enabled[task]]


def _log_result(result: ModuleResult) -> None:
    level = {
        Status.SUCCESS: logging.INFO,
        Status.SKIPPED: logging.INFO,
        Status.WARNING: logging.WARNING,
        Status.FAILED: logging.ERROR,
    }[result.status]
    logger.log(level, "Module %s finished: %s (%s)", result.module, result.status, result.summary)


def _safe_step(module: str, action: Callable[[], ModuleResult]) -> ModuleResult:
    """Executa um módulo; exceções inesperadas viram FAILED sem interromper os demais."""
    started = time.monotonic()
    try:
        result = action()
    except Exception as exc:
        logger.exception("Unexpected error in module %s", module)
        result = ModuleResult(
            module,
            Status.FAILED,
            f"Erro inesperado: {exc}",
            errors=[f"{type(exc).__name__}: {exc}"],
        )
    if not result.duration_seconds:
        result.duration_seconds = time.monotonic() - started
    _log_result(result)
    return result


def _log_system_info(info: SystemInfo) -> None:
    logger.info("Hostname: %s", info.hostname)
    logger.info("User: %s", info.username)
    logger.info("Windows: %s", info.windows.full)
    logger.info("Architecture: %s", info.architecture)
    if info.disk:
        logger.info(
            "Disk %s %s free of %s",
            info.disk.drive,
            format_bytes(info.disk.free_bytes),
            format_bytes(info.disk.total_bytes),
        )


def _check_winget(services: Services) -> tuple[WingetInfo | None, ModuleResult]:
    logger.info("Checking Winget")
    info = get_winget_info(services.find_winget(), runner=services.run_command)
    result = winget_result(info)
    _log_result(result)
    return info, result


def run_check(
    config: AppConfig,
    paths: AppPaths,
    *,
    tasks: Sequence[str] = (),
    verbose: bool = False,
    services: Services | None = None,
    on_result: Callable[[ModuleResult], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> CheckOutcome:
    """Verifica o estado da máquina sem alterar nada (Chrome, WU e limpeza em modo leitura)."""
    ensure_supported_platform()
    services = services or Services()
    selected = select_tasks(config, tasks)
    started = services.now()
    info = services.collect_system_info(services.registry)
    log_file: Path | None = (
        paths.logs_dir / f"{run_file_stem(info.hostname, started, '_check')}.log"
    )
    try:
        configure_logging(log_file, console=False, verbose=verbose)
    except OSError:
        log_file = None  # o check é somente leitura: segue sem arquivo de log
        configure_logging(None, console=False)

    results: list[ModuleResult] = []

    def record(result: ModuleResult) -> None:
        results.append(result)
        if on_result:
            on_result(result)

    try:
        logger.info("Check started (agent v%s, read-only)", __version__)
        _log_system_info(info)
        privileges = services.get_privileges()
        record(_safe_step("privileges", lambda: privilege_result(privileges)))
        winget_info, winget_status = _check_winget(services)
        record(winget_status)
        winget_path = winget_info.path if winget_info else None
        if TASK_CHROME in selected:
            record(
                _safe_step(
                    TASK_CHROME,
                    lambda: chrome.process_chrome(
                        services.registry,
                        winget_path,
                        apply=False,
                        is_admin=privileges.is_admin,
                        timeout=config.winget_timeout_seconds,
                        runner=services.run_command,
                    ),
                )
            )
        if TASK_WINDOWS_UPDATE in selected:
            if progress:
                progress("Pesquisando atualizações do Windows (pode levar alguns minutos)...")
            record(
                _safe_step(
                    TASK_WINDOWS_UPDATE,
                    lambda: _windows_update(
                        config, services, privileges, apply=False, dry_run=False
                    ),
                )
            )
        record(
            _safe_step(
                "disk",
                lambda: evaluate_disk_space(
                    info.disk,
                    min_free_percent=config.disk_min_free_percent,
                    min_free_gb=config.disk_min_free_gb,
                ),
            )
        )
        if TASK_CLEANUP in selected:
            if progress:
                progress("Estimando arquivos temporários removíveis...")
            record(
                _safe_step(
                    TASK_CLEANUP,
                    lambda: _cleanup(config, services, privileges, apply=False, dry_run=False),
                )
            )
        record(
            _safe_step("reboot", lambda: reboot_result(detect_pending_reboot(services.registry)))
        )
        logger.info("Check finished")
    finally:
        close_logging()
    return CheckOutcome(info, privileges, results, log_file)


def _windows_update(
    config: AppConfig,
    services: Services,
    privileges: PrivilegeInfo,
    *,
    apply: bool,
    dry_run: bool,
) -> ModuleResult:
    return windows_update.process_windows_update(
        apply=apply,
        is_admin=privileges.is_admin,
        dry_run=dry_run,
        include_optional=config.windows_update_include_optional,
        accept_eulas=config.windows_update_accept_eulas,
        search_timeout=config.windows_update_search_timeout_minutes * 60,
        install_timeout=config.windows_update_install_timeout_minutes * 60,
        client_factory=services.wua_client_factory,
    )


def _cleanup(
    config: AppConfig,
    services: Services,
    privileges: PrivilegeInfo,
    *,
    apply: bool,
    dry_run: bool,
) -> ModuleResult:
    return cleanup.process_cleanup(
        config.cleanup_targets,
        apply=apply,
        dry_run=dry_run,
        is_admin=privileges.is_admin,
        registry=services.registry,
        min_age_days=config.cleanup_min_age_days,
        env=services.cleanup_env,
        windows_dir=services.windows_dir,
    )


class RunLock:
    """Impede duas manutenções simultâneas (ex.: tarefa agendada + execução manual).

    O bloqueio é liberado pelo Windows se o processo terminar inesperadamente.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        import msvcrt

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # permanece aberto enquanto durar o bloqueio
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        import msvcrt

        try:
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None


def run_maintenance(
    config: AppConfig,
    paths: AppPaths,
    options: RunOptions | None = None,
    services: Services | None = None,
    *,
    on_result: Callable[[ModuleResult], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunOutcome:
    """Executa a manutenção. Lança :class:`CriticalError` se não puder começar.

    ``on_result`` recebe o resultado de cada etapa assim que ela termina e ``progress``
    recebe uma mensagem antes de cada etapa (usados pela interface gráfica).
    """
    ensure_supported_platform()
    options = options or RunOptions()
    services = services or Services()
    try:
        paths.ensure()
    except OSError as exc:
        raise CriticalError(
            f"não foi possível criar os diretórios de trabalho em {paths.base_dir}: {exc}"
        ) from exc
    lock = RunLock(paths.lock_file)
    if not lock.acquire():
        raise CriticalError("outra manutenção já está em execução nesta máquina")
    try:
        return _run_locked(config, paths, options, services, on_result, progress)
    finally:
        lock.release()
        close_logging()


def _start_history(
    paths: AppPaths,
    info: SystemInfo,
    started: datetime,
    tasks: list[str],
    options: RunOptions,
    log_file: Path,
) -> tuple[HistoryStore | None, int | None]:
    store = HistoryStore(paths.history_db)
    try:
        store.initialize()
        interrupted = store.mark_interrupted()
        if interrupted:
            logger.warning("%d previous run(s) marked as INTERRUPTED", interrupted)
        run_id = store.start_run(
            hostname=info.hostname,
            started_at=started,
            tasks=tasks,
            dry_run=options.dry_run,
            agent_version=__version__,
            log_file=str(log_file),
        )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("History database unavailable: %s", exc)
        return None, None
    logger.info("Run ID: %d", run_id)
    return store, run_id


def _not_requested(task: str, tasks_from_flags: bool) -> ModuleResult:
    reason = "Não solicitado nesta execução" if tasks_from_flags else "Desabilitado na configuração"
    return ModuleResult(task, Status.SKIPPED, reason)


def _run_locked(
    config: AppConfig,
    paths: AppPaths,
    options: RunOptions,
    services: Services,
    on_result: Callable[[ModuleResult], None] | None = None,
    progress: Callable[[str], None] | None = None,
) -> RunOutcome:
    results: list[ModuleResult] = []

    def step(message: str) -> None:
        if progress:
            progress(message)

    def record(result: ModuleResult) -> None:
        results.append(result)
        if on_result:
            on_result(result)

    started = services.now()
    clock = time.monotonic()
    selected = select_tasks(config, options.tasks)
    step("Coletando informações do sistema...")
    info = services.collect_system_info(services.registry)
    stem = run_file_stem(info.hostname, started)
    log_file = paths.logs_dir / f"{stem}.log"
    try:
        configure_logging(log_file, console=options.console, verbose=options.verbose)
    except OSError as exc:
        raise CriticalError(f"não foi possível criar o arquivo de log {log_file}: {exc}") from exc

    logger.info("Maintenance started")
    logger.info("Agent version: %s", __version__)
    if options.dry_run:
        logger.info("Mode: DRY-RUN (no changes will be made)")
    logger.info("Tasks: %s", ", ".join(selected) or "none")
    _log_system_info(info)
    history, run_id = _start_history(paths, info, started, selected, options, log_file)

    reboot_status = RebootStatus(required=False)
    disk_after: DiskUsage | None = info.disk
    privileges = PrivilegeInfo(is_admin=False)
    critical_error: str | None = None
    simulation = " (simulação)" if options.dry_run else ""
    try:
        record(ModuleResult("system_info", Status.SUCCESS, "Informações coletadas", info.to_dict()))
        step("Verificando privilégios administrativos...")
        logger.info("Checking privileges")
        privileges = services.get_privileges()
        record(privilege_result(privileges))
        _log_result(results[-1])
        step("Verificando o winget...")
        winget_info, winget_status = _check_winget(services)
        record(winget_status)
        winget_path = winget_info.path if winget_info else None

        if TASK_CHROME in selected:
            step(f"Atualizando o Google Chrome{simulation}...")
            record(
                _safe_step(
                    TASK_CHROME,
                    lambda: chrome.process_chrome(
                        services.registry,
                        winget_path,
                        apply=True,
                        is_admin=privileges.is_admin,
                        dry_run=options.dry_run,
                        timeout=config.winget_timeout_seconds,
                        runner=services.run_command,
                    ),
                )
            )
        else:
            record(_not_requested(TASK_CHROME, bool(options.tasks)))

        if TASK_WINDOWS_UPDATE in selected:
            step(f"Processando atualizações do Windows{simulation} (pode levar vários minutos)...")
            record(
                _safe_step(
                    TASK_WINDOWS_UPDATE,
                    lambda: _windows_update(
                        config, services, privileges, apply=True, dry_run=options.dry_run
                    ),
                )
            )
        else:
            record(_not_requested(TASK_WINDOWS_UPDATE, bool(options.tasks)))

        step("Verificando reinicialização pendente...")
        logger.info("Checking pending reboot")
        installation_reboot = any(result.details.get("reboot_required") for result in results)
        reboot_status = detect_pending_reboot(services.registry).with_installation_reboot(
            installation_reboot
        )
        record(reboot_result(reboot_status))
        _log_result(results[-1])
        if reboot_status.required:
            logger.warning("REBOOT_REQUIRED = true")
            logger.warning(reboot_status.message)

        if TASK_CLEANUP in selected:
            step(f"Limpando arquivos temporários{simulation}...")
            record(
                _safe_step(
                    TASK_CLEANUP,
                    lambda: _cleanup(
                        config, services, privileges, apply=True, dry_run=options.dry_run
                    ),
                )
            )
        else:
            record(_not_requested(TASK_CLEANUP, bool(options.tasks)))

        step("Verificando o espaço em disco...")
        logger.info("Checking disk space")
        drive = info.disk.drive if info.disk else services.system_drive()
        disk_after = services.disk_usage(drive)
        record(
            evaluate_disk_space(
                disk_after,
                min_free_percent=config.disk_min_free_percent,
                min_free_gb=config.disk_min_free_gb,
            )
        )
        _log_result(results[-1])
    except KeyboardInterrupt:
        logger.error("Maintenance interrupted by user")
        if history is not None:
            history.mark_interrupted()
        raise
    except Exception as exc:  # erro crítico no próprio fluxo: aborta, mas gera relatório
        logger.exception("Critical error: maintenance aborted")
        critical_error = f"{type(exc).__name__}: {exc}"

    step("Gerando relatório...")
    overall = aggregate(
        results,
        requested_tasks=selected,
        reboot_required=reboot_status.required,
        critical_error=critical_error is not None,
    )
    finished = services.now()
    duration = time.monotonic() - clock
    level = SUCCESS if overall is OverallResult.SUCCESS else logging.WARNING
    if overall is OverallResult.FAILED:
        level = logging.ERROR
    logger.log(level, "Maintenance finished: %s (%s)", overall, format_duration(duration))
    flush_logging()

    report = MaintenanceReport(
        agent_version=__version__,
        hostname=info.hostname,
        username=info.username,
        windows=info.windows.full,
        admin_label=privileges.label(),
        started_at=started,
        finished_at=finished,
        overall=overall,
        modules=results,
        requested_tasks=selected,
        dry_run=options.dry_run,
        reboot=reboot_status,
        disk_before=info.disk,
        disk_after=disk_after,
        run_id=run_id,
        log_file=str(log_file),
        critical_error=critical_error,
    )
    report_file: Path | None = None
    json_file: Path | None = None
    try:
        report_file, json_file = write_report(report, paths.reports_dir, stem)
        logger.info("Report saved: %s", report_file)
    except OSError as exc:
        logger.error("Could not write report: %s", exc)

    if history is not None and run_id is not None:
        try:
            history.finish_run(
                run_id,
                finished_at=finished,
                duration_seconds=duration,
                result=str(overall),
                chrome_updated=_chrome_updated(report.module(TASK_CHROME)),
                windows_updates_installed=_details_int(
                    report, TASK_WINDOWS_UPDATE, "updates_installed"
                ),
                cleanup_performed=bool(_details(report, TASK_CLEANUP).get("cleanup_performed")),
                bytes_removed=_details_int(report, TASK_CLEANUP, "bytes_removed"),
                reboot_required=reboot_status.required,
                report_file=str(report_file) if report_file else None,
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Could not update history: %s", exc)

    for directory in (paths.logs_dir, paths.reports_dir):
        removed = purge_old_files(directory, retention_days=config.log_retention_days)
        if removed:
            logger.info("Retention: %d old file(s) removed from %s", removed, directory.name)

    if config.network_log_path and not options.dry_run:
        step("Copiando log e relatórios para o compartilhamento de rede...")
    network = _network_sync(config, info, options, [log_file, report_file, json_file])
    if history is not None and run_id is not None and network.status is not Status.SKIPPED:
        try:
            history.set_network_sync(run_id, str(network.status))
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Could not update history: %s", exc)

    return RunOutcome(overall, report, log_file, report_file, json_file, run_id, network)


def _details(report: MaintenanceReport, module: str) -> dict:
    result = report.module(module)
    return result.details if result else {}


def _details_int(report: MaintenanceReport, module: str, key: str) -> int:
    return int(_details(report, module).get(key) or 0)


def _chrome_updated(result: ModuleResult | None) -> bool | None:
    if result is None or result.status is Status.SKIPPED:
        return None
    return bool(result.details.get("updated"))


def _network_sync(
    config: AppConfig, info: SystemInfo, options: RunOptions, files: list[Path | None]
) -> ModuleResult:
    if not config.network_log_path:
        return ModuleResult("network_sync", Status.SKIPPED, "Sincronização de rede não configurada")
    if options.dry_run:
        logger.info("Network log synchronization skipped (dry-run)")
        return ModuleResult("network_sync", Status.SKIPPED, "Sincronização ignorada (dry-run)")
    return sync_files(
        config.network_log_path, info.hostname, files, timeout=config.network_timeout_seconds
    )
