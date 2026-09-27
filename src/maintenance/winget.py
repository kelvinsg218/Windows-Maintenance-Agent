"""Integração com o Windows Package Manager (winget).

Os comandos são montados apenas a partir de constantes do agente (IDs de pacote e
origem validados por expressão regular), executados sem shell e com timeout.
A saída do winget é localizada (ex.: pt-BR), por isso o parser identifica as
colunas pela posição, a partir da linha de traços que separa o cabeçalho.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from maintenance.process import CommandResult, CommandRunner, format_exit_code, run_command
from maintenance.results import ModuleResult, Status

logger = logging.getLogger(__name__)

WINGET_SOURCE = "winget"

NO_APPLICATIONS_FOUND = 0x8A150014
UPDATE_NOT_APPLICABLE = 0x8A15002B
REBOOT_REQUIRED_TO_FINISH = 0x8A150109

# Descrições conferidas com "winget error --input <código>".
EXIT_CODE_MESSAGES: dict[int, str] = {
    NO_APPLICATIONS_FOUND: "nenhum pacote instalado corresponde ao ID",
    UPDATE_NOT_APPLICABLE: "nenhuma atualização aplicável encontrada",
    0x8A150008: "falha no download do instalador",
    0x8A15000F: "dados da origem ausentes (execute 'winget source update')",
    0x8A150011: "hash do instalador não corresponde ao manifesto (instalação bloqueada)",
    0x8A150019: "o comando requer privilégios de administrador",
    0x8A150056: "o instalador não pode ser executado em contexto de administrador",
    0x8A150101: "o aplicativo está em execução; feche-o e tente novamente",
    0x8A150102: "outra instalação já está em andamento",
    0x8A150103: "arquivos do aplicativo estão em uso",
    0x8A150105: "espaço em disco insuficiente",
    0x8A150107: "sem conectividade de rede",
    REBOOT_REQUIRED_TO_FINISH: "reinicialização necessária para concluir a instalação",
    0x8A15010A: "reinicie o computador e tente novamente",
    0x8A15010F: "instalação bloqueada por política da organização",
}

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}$")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_VERSION = re.compile(r"v?(\d+(?:\.\d+)+)")
_SEPARATOR_CHARS = {"-", "\u2500"}
_PACKAGE_DIR_PATTERNS = (
    "Microsoft.DesktopAppInstaller_*_x64__8wekyb3d8bbwe",
    "Microsoft.DesktopAppInstaller_*_arm64__8wekyb3d8bbwe",
)


class WingetError(Exception):
    """Falha ao executar ou interpretar um comando do winget."""


@dataclass(frozen=True)
class WingetInfo:
    path: Path
    version: str | None


@dataclass(frozen=True)
class WingetPackage:
    name: str
    package_id: str
    version: str
    available: str | None
    source: str | None


def describe_exit_code(code: int | None) -> str:
    message = EXIT_CODE_MESSAGES.get(code) if code is not None else None
    return f"{format_exit_code(code)} ({message})" if message else format_exit_code(code)


def find_winget(env: Mapping[str, str] | None = None) -> Path | None:
    """Localiza o winget.exe em locais conhecidos (nunca a partir de entrada externa).

    Ordem: PATH, alias de execução do usuário (``%LOCALAPPDATA%\\Microsoft\\WindowsApps``)
    e pasta do pacote App Installer — este último é o único caminho disponível quando o
    agente roda como SYSTEM (Agendador de Tarefas).
    """
    env = os.environ if env is None else env
    candidates: list[Path] = []
    found = shutil.which("winget")
    if found:
        candidates.append(Path(found))
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data, "Microsoft", "WindowsApps", "winget.exe"))
    candidates.extend(_package_dir_candidates(env))
    for candidate in candidates:
        if candidate.name.lower() == "winget.exe" and candidate.is_file():
            return candidate
    return None


def _package_dir_candidates(env: Mapping[str, str]) -> list[Path]:
    program_files = env.get("ProgramFiles") or r"C:\Program Files"
    matches: list[str] = []
    for pattern in _PACKAGE_DIR_PATTERNS:
        try:
            matches.extend(glob.glob(os.path.join(program_files, "WindowsApps", pattern)))
        except OSError:  # usuários comuns não podem listar WindowsApps
            continue
    folders = sorted((Path(m) for m in matches), key=package_version_key, reverse=True)
    return [folder / "winget.exe" for folder in folders]


def package_version_key(folder: Path) -> tuple[int, ...]:
    """Chave de ordenação pela versão no nome da pasta do pacote (…_1.29.380.0_x64__…)."""
    parts = folder.name.split("_")
    if len(parts) < 2:
        return ()
    try:
        return tuple(int(piece) for piece in parts[1].split("."))
    except ValueError:
        return ()


def _cwd_for(winget: Path) -> Path | None:
    # Executado a partir da pasta do pacote (conta SYSTEM), o winget precisa dela como
    # diretório de trabalho para localizar suas dependências.
    return winget.parent if "windowsapps" in (p.lower() for p in winget.parts[:-2]) else None


def get_winget_info(
    winget: Path | None, *, runner: CommandRunner = run_command, timeout: float = 60
) -> WingetInfo | None:
    if winget is None:
        return None
    result = runner([str(winget), "--version"], timeout=timeout, cwd=_cwd_for(winget))
    match = _VERSION.search(result.stdout) if result.ok else None
    return WingetInfo(winget, match.group(1) if match else None)


def winget_result(info: WingetInfo | None) -> ModuleResult:
    if info is None:
        return ModuleResult(
            "winget",
            Status.WARNING,
            "Winget não encontrado (instale/atualize o App Installer da Microsoft)",
            {"available": False},
        )
    version = f" (v{info.version})" if info.version else ""
    details = {"available": True, "version": info.version, "path": str(info.path)}
    return ModuleResult("winget", Status.SUCCESS, f"Winget disponível{version}", details)


def clean_output(text: str) -> list[str]:
    """Remove indicadores de progresso (sobrescritos com \\r) e sequências ANSI."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        visible = _ANSI_ESCAPE.sub("", raw.rsplit("\r", 1)[-1])
        lines.append(visible.rstrip())
    return lines


def _column_layout(count: int, *, source_filtered: bool) -> tuple[int | None, int | None]:
    """Índices das colunas (Disponível, Origem) conforme o número de colunas da tabela.

    Colunas: Nome, ID, Versão, [Disponível], [Origem]. "Disponível" só aparece se algum
    pacote tem atualização; "Origem" é omitida quando o comando usa ``--source``.
    """
    if count == 5:
        return 3, 4
    if count == 4:
        return (3, None) if source_filtered else (None, 3)
    return None, None


def parse_list_output(text: str, *, source_filtered: bool = False) -> list[WingetPackage]:
    """Interpreta a tabela de ``winget list``.

    ``source_filtered`` indica que o comando foi executado com ``--source`` (sem a coluna
    Origem): com 4 colunas, a quarta é então "Disponível".
    """
    lines = clean_output(text)
    separator = next(
        (
            index
            for index, line in enumerate(lines)
            if index > 0 and len(line.strip()) >= 10 and set(line.strip()) <= _SEPARATOR_CHARS
        ),
        None,
    )
    if separator is None:
        return []
    header = lines[separator - 1]
    starts = [match.start() for match in re.finditer(r"\S+", header)]
    if len(starts) not in (3, 4, 5):
        raise WingetError(f"formato de tabela do winget não reconhecido: {header.strip()!r}")
    available_index, source_index = _column_layout(len(starts), source_filtered=source_filtered)
    packages: list[WingetPackage] = []
    for line in lines[separator + 1 :]:
        if len(line) <= starts[1]:
            continue
        bounds = list(zip(starts, [*starts[1:], len(line)], strict=True))
        cells = [line[begin:end].strip() for begin, end in bounds]
        if not cells[1]:
            continue
        packages.append(
            WingetPackage(
                name=cells[0],
                package_id=cells[1],
                version=cells[2],
                available=(cells[available_index] or None) if available_index else None,
                source=(cells[source_index] or None) if source_index else None,
            )
        )
    return packages


def find_package(
    text: str, package_id: str, *, source_filtered: bool = False
) -> WingetPackage | None:
    for package in parse_list_output(text, source_filtered=source_filtered):
        if package.package_id.lower() == package_id.lower():
            return package
    return None


def _checked(value: str, what: str) -> str:
    if not _SAFE_TOKEN.match(value):
        raise ValueError(f"{what} inválido: {value!r}")
    return value


def list_package(
    winget: Path,
    package_id: str,
    *,
    timeout: float,
    runner: CommandRunner = run_command,
    source: str = WINGET_SOURCE,
) -> WingetPackage | None:
    """Consulta um pacote instalado pelo ID exato. ``None`` = não instalado/correlacionado."""
    args = [
        str(winget),
        "list",
        "--id",
        _checked(package_id, "ID de pacote"),
        "--exact",
        "--source",
        _checked(source, "origem"),
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    logger.debug("Running: %s", " ".join(args))
    result = runner(args, timeout=timeout, cwd=_cwd_for(winget))
    if result.exit_code == NO_APPLICATIONS_FOUND:
        return None
    if not result.ok:
        raise WingetError(_failure_message("winget list", result))
    # Com --source o winget omite a coluna "Origem".
    return find_package(result.stdout, package_id, source_filtered=True)


def upgrade_package(
    winget: Path,
    package_id: str,
    *,
    timeout: float,
    runner: CommandRunner = run_command,
    source: str = WINGET_SOURCE,
) -> CommandResult:
    """Atualiza silenciosamente um pacote pelo ID exato. O chamador interpreta o resultado."""
    args = [
        str(winget),
        "upgrade",
        "--id",
        _checked(package_id, "ID de pacote"),
        "--exact",
        "--source",
        _checked(source, "origem"),
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    logger.debug("Running: %s", " ".join(args))
    result = runner(args, timeout=timeout, cwd=_cwd_for(winget))
    logger.debug(
        "winget upgrade finished: exit=%s duration=%.1fs",
        format_exit_code(result.exit_code),
        result.duration_seconds,
    )
    return result


def _failure_message(command: str, result: CommandResult) -> str:
    if result.error or result.timed_out:
        return f"{command}: {result.describe_exit()}"
    return f"{command}: {describe_exit_code(result.exit_code)}"


def output_tail(result: CommandResult, max_lines: int = 5) -> str:
    """Últimas linhas úteis de stdout/stderr, para o log de erros."""
    lines = [line for line in clean_output(result.stdout + "\n" + result.stderr) if line.strip()]
    return " | ".join(lines[-max_lines:])
