"""Textos da interface gráfica (português brasileiro).

O código e os identificadores internos continuam em inglês; aqui ficam apenas os
textos exibidos ao usuário e o mapeamento dos estados internos para rótulos legíveis.
"""

from __future__ import annotations

from maintenance.history import RESULT_INTERRUPTED, RESULT_RUNNING
from maintenance.reboot import (
    SOURCE_CBS,
    SOURCE_COMPUTER_RENAME,
    SOURCE_FILE_RENAME,
    SOURCE_INSTALLATION,
    SOURCE_UPDATE_EXE,
    SOURCE_WINDOWS_UPDATE,
)
from maintenance.results import TASK_CHROME, TASK_CLEANUP, TASK_WINDOWS_UPDATE, OverallResult

PAGE_DASHBOARD = "dashboard"
PAGE_CHECK = "check"
PAGE_MAINTENANCE = "maintenance"
PAGE_HISTORY = "history"

PAGE_TITLES: dict[str, str] = {
    PAGE_DASHBOARD: "Painel",
    PAGE_CHECK: "Verificação do sistema",
    PAGE_MAINTENANCE: "Manutenção",
    PAGE_HISTORY: "Histórico",
}

PAGE_SUBTITLES: dict[str, str] = {
    PAGE_DASHBOARD: "Informações do computador e estado geral da manutenção.",
    PAGE_CHECK: "Analisa o computador e aponta pendências. Nenhuma alteração é feita.",
    PAGE_MAINTENANCE: "Escolha as rotinas que deseja executar. Nada é feito sem a sua confirmação.",
    PAGE_HISTORY: "Manutenções executadas neste computador.",
}

MODULE_TITLES: dict[str, str] = {
    "system_info": "Informações do sistema",
    "privileges": "Privilégios de administrador",
    "winget": "Gerenciador de pacotes (winget)",
    TASK_CHROME: "Google Chrome",
    TASK_WINDOWS_UPDATE: "Windows Update",
    "disk": "Espaço em disco",
    TASK_CLEANUP: "Arquivos temporários",
    "reboot": "Reinicialização pendente",
    "network_sync": "Sincronização com a rede",
    "critical_error": "Erro crítico",
}

TASK_LABELS: dict[str, str] = {
    TASK_CHROME: "Google Chrome",
    TASK_WINDOWS_UPDATE: "Windows Update",
    TASK_CLEANUP: "Limpeza de temporários",
}

# Rótulos curtos dos estados (selos ao lado de cada resultado).
LEVEL_LABELS: dict[str, str] = {
    "ok": "OK",
    "warning": "Atenção",
    "error": "Erro",
    "info": "Informação",
    "skipped": "Ignorado",
}
UPDATE_AVAILABLE = "Atualização disponível"

OVERALL_TITLES: dict[OverallResult, str] = {
    OverallResult.SUCCESS: "Manutenção concluída",
    OverallResult.SUCCESS_WITH_WARNINGS: "Manutenção concluída com pendências",
    OverallResult.FAILED: "A manutenção falhou",
}
OVERALL_LEVELS: dict[OverallResult, str] = {
    OverallResult.SUCCESS: "ok",
    OverallResult.SUCCESS_WITH_WARNINGS: "warning",
    OverallResult.FAILED: "error",
}

HISTORY_RESULTS: dict[str, tuple[str, str]] = {
    OverallResult.SUCCESS.value: ("Sucesso", "ok"),
    OverallResult.SUCCESS_WITH_WARNINGS.value: ("Sucesso com pendências", "warning"),
    OverallResult.FAILED.value: ("Falha", "error"),
    RESULT_RUNNING: ("Em andamento", "info"),
    RESULT_INTERRUPTED: ("Interrompida", "error"),
}

REBOOT_SOURCES: dict[str, str] = {
    SOURCE_CBS: "componentes do Windows",
    SOURCE_WINDOWS_UPDATE: "Windows Update",
    SOURCE_UPDATE_EXE: "instalação de atualizações",
    SOURCE_FILE_RENAME: "substituição de arquivos em uso",
    SOURCE_COMPUTER_RENAME: "renomeação do computador",
    SOURCE_INSTALLATION: "atualizações instaladas nesta manutenção",
}

PRIVILEGES_ADMIN = "O aplicativo está sendo executado com privilégios de administrador."
PRIVILEGES_LIMITED = (
    "Sem privilégios de administrador: a instalação de atualizações do Windows, a "
    "atualização de programas instalados para todos os usuários e a limpeza de pastas do "
    "sistema serão ignoradas. Use “Reiniciar como administrador” quando precisar delas."
)


def history_result(result: str) -> tuple[str, str]:
    """Rótulo e nível visual de um resultado gravado no histórico."""
    return HISTORY_RESULTS.get(result, (result, "info"))


def task_list(tasks: str) -> str:
    """``chrome,cleanup`` -> ``Google Chrome, Limpeza de temporários``."""
    names = [TASK_LABELS.get(task, task) for task in tasks.split(",") if task]
    return ", ".join(names) if names else "Nenhuma"


def display_text(text: str) -> str:
    """Ajusta resumos técnicos dos módulos ao vocabulário da interface."""
    return text.replace("(dry-run)", "(simulação)").replace("dry-run", "simulação")


def reboot_sources(sources: list[str] | tuple[str, ...]) -> str:
    return ", ".join(REBOOT_SOURCES.get(source, source) for source in sources)


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


def sentence(text: str) -> str:
    """Primeira letra maiúscula, sem alterar o restante (ao contrário de ``str.capitalize``)."""
    return text[:1].upper() + text[1:]
