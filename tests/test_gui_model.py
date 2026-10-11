"""Testes da lógica de apresentação da interface gráfica (sem abrir janelas)."""

import json

import pytest

from maintenance.gui import model, texts
from maintenance.history import RESULT_INTERRUPTED, RESULT_RUNNING, HistoryStore
from maintenance.reboot import SOURCE_CBS, SOURCE_FILE_RENAME, RebootStatus
from maintenance.registry import HKLM
from maintenance.results import ModuleResult, OverallResult, Status
from maintenance.runner import RunOptions, run_check, run_maintenance
from maintenance.system_info import PROCESSOR_KEY, MemoryStatus
from tests.fake_env import GB, build_environment
from tests.wua_fakes import FakeUpdate


def test_check_steps_follow_runner_order():
    assert model.check_steps(["chrome", "windows_update", "cleanup"]) == list(model.CHECK_ORDER)
    assert model.check_steps(["cleanup"]) == ["privileges", "winget", "disk", "cleanup", "reboot"]
    assert set(model.CHECK_STEP_MESSAGES) == set(model.CHECK_ORDER)


def test_check_results_match_planned_steps(tmp_path):
    env = build_environment(tmp_path)
    outcome = run_check(env.config, env.paths, tasks=("chrome",), services=env.services)
    assert [result.module for result in outcome.results] == model.check_steps(["chrome"])


def test_presentation_of_privileges_and_updates():
    limited = model.present_result(
        ModuleResult("privileges", Status.WARNING, "x", {"is_admin": False})
    )
    assert limited.level == "info"
    assert "Reiniciar como administrador" in limited.text
    assert "terminal" not in limited.text
    admin = model.present_result(
        ModuleResult("privileges", Status.SUCCESS, "x", {"is_admin": True})
    )
    assert admin.level == "ok" and admin.badge == "OK"

    updates = ModuleResult(
        "windows_update",
        Status.WARNING,
        "2 atualizações do Windows encontradas",
        {
            "updates_available": 1,
            "updates": [
                {"title": "Cumulativa (KB1)", "eligible": True, "manual_action": False},
                {"title": "Com licença (KB2)", "eligible": False, "manual_action": True},
                {"title": "Opcional (KB3)", "eligible": False, "manual_action": False},
            ],
        },
    )
    presentation = model.present_result(updates)
    assert presentation.badge == texts.UPDATE_AVAILABLE
    assert presentation.details == [
        "Pendente: Cumulativa (KB1)",
        "Requer ação manual: Com licença (KB2)",
    ]
    installed = model.present_result(
        ModuleResult("windows_update", Status.SUCCESS, "1 instalada", {"installed": ["KB1"]}),
        applied=True,
    )
    assert installed.details == ["Instalada: KB1"]


def test_long_lists_are_truncated():
    updates = [{"title": f"KB{i}", "eligible": True} for i in range(12)]
    result = ModuleResult("windows_update", Status.WARNING, "x", {"updates": updates})
    details = model.present_result(result).details
    assert len(details) == model.MAX_LISTED_ITEMS + 1
    assert details[-1] == "... e mais 4"


def test_reboot_and_dry_run_texts_are_translated():
    reboot = model.present_result(
        ModuleResult(
            "reboot",
            Status.WARNING,
            "Reinicialização necessária (Component Based Servicing)",
            RebootStatus(True, (SOURCE_CBS, SOURCE_FILE_RENAME)).to_dict(),
        )
    )
    assert "componentes do Windows, substituição de arquivos em uso" in reboot.text
    assert "Component Based Servicing" not in reboot.text
    dry = model.present_result(
        ModuleResult("cleanup", Status.SKIPPED, "4.0 KB seriam removidos (dry-run)", {}),
        applied=True,
    )
    assert dry.text == "4.0 KB seriam removidos (simulação)"
    assert dry.title == "Limpeza de arquivos temporários"
    assert dry.level == "skipped" and dry.badge == "Ignorado"


def test_cleanup_details_for_check_and_maintenance(tmp_path):
    env = build_environment(tmp_path)
    check = run_check(env.config, env.paths, tasks=("cleanup",), services=env.services)
    estimate = model.present_result(next(r for r in check.results if r.module == "cleanup"))
    assert estimate.details[0].startswith("1 de 2 arquivos analisados podem ser removidos")

    run = run_maintenance(
        env.config, env.paths, RunOptions(tasks=("cleanup",), console=False), env.services
    )
    done = model.present_result(run.report.module("cleanup"), applied=True)
    assert done.details[0].startswith("1 de 2 arquivos analisados foram removidos (4.0 KB)")
    assert any("requer privilégios administrativos" in line for line in done.details)


def test_visible_results_hide_internal_and_unrequested_steps(tmp_path):
    env = build_environment(tmp_path)
    outcome = run_maintenance(
        env.config, env.paths, RunOptions(tasks=("cleanup",), console=False), env.services
    )
    visible = [r.module for r in model.visible_results(outcome.report.modules, ["cleanup"])]
    assert visible == ["privileges", "winget", "reboot", "cleanup", "disk"]


@pytest.mark.parametrize(
    ("result", "is_admin", "expected"),
    [
        (ModuleResult("privileges", Status.SUCCESS, "x", {"is_admin": True}), True, None),
        (ModuleResult("privileges", Status.WARNING, "x"), False, ("info", None, False)),
        (ModuleResult("winget", Status.WARNING, "x"), False, ("warning", None, False)),
        (
            ModuleResult(
                "chrome", Status.WARNING, "x", {"updates_available": 1, "scope": "machine"}
            ),
            False,
            ("warning", "chrome", True),
        ),
        (
            ModuleResult("chrome", Status.WARNING, "x", {"updates_available": 1, "scope": "user"}),
            False,
            ("warning", "chrome", False),
        ),
        (ModuleResult("chrome", Status.SUCCESS, "x"), False, None),
        (ModuleResult("chrome", Status.FAILED, "x"), False, ("error", None, False)),
        (
            ModuleResult("windows_update", Status.WARNING, "x", {"updates_available": 3}),
            True,
            ("warning", "windows_update", False),
        ),
        (
            ModuleResult("windows_update", Status.WARNING, "x", {"updates_available": 3}),
            False,
            ("warning", "windows_update", True),
        ),
        (
            ModuleResult("windows_update", Status.WARNING, "x", {"manual_action": 1}),
            True,
            ("warning", None, False),
        ),
        (ModuleResult("windows_update", Status.SUCCESS, "x"), True, None),
        (
            ModuleResult("cleanup", Status.SUCCESS, "x", {"bytes_eligible": 2048}),
            False,
            ("info", "cleanup", False),
        ),
        (
            ModuleResult("cleanup", Status.WARNING, "x", {"bytes_eligible": 2 * GB}),
            False,
            ("warning", "cleanup", False),
        ),
        (ModuleResult("cleanup", Status.SUCCESS, "x", {"bytes_eligible": 0}), False, None),
        (ModuleResult("disk", Status.WARNING, "x"), False, ("warning", "cleanup", False)),
        (ModuleResult("reboot", Status.WARNING, "x"), False, ("warning", None, False)),
        (ModuleResult("reboot", Status.SUCCESS, "x"), False, None),
        (ModuleResult("disk", Status.FAILED, "x"), False, ("error", None, False)),
    ],
)
def test_recommendations(result, is_admin, expected):
    recommendation = model.recommend(result, is_admin=is_admin)
    if expected is None:
        assert recommendation is None
    else:
        level, task, needs_admin = expected
        assert (recommendation.level, recommendation.task, recommendation.needs_admin) == (
            level,
            task,
            needs_admin,
        )
        assert recommendation.text


def test_summarize_check(tmp_path):
    env = build_environment(tmp_path)
    outcome = run_check(env.config, env.paths, services=env.services)
    summary = model.summarize_check(outcome)
    assert summary.level == "warning"
    # Chrome e Windows Update com atualização; privilégios não contam como problema.
    assert summary.problems == 2
    assert summary.title == "2 pontos de atenção encontrados"
    assert summary.text == "Atualizações disponíveis: 2 · Espaço recuperável estimado: 4.0 KB"

    clean = build_environment(tmp_path / "clean", admin=True, chrome_available="", wua_updates=[])
    outcome = run_check(clean.config, clean.paths, tasks=("chrome",), services=clean.services)
    assert model.summarize_check(outcome).level == "ok"
    assert model.summarize_check(outcome).title == "Nenhum problema encontrado"


def test_summarize_failed_check(tmp_path):
    env = build_environment(tmp_path, wua_options={"search_error": RuntimeError("offline")})
    outcome = run_check(env.config, env.paths, tasks=("windows_update",), services=env.services)
    summary = model.summarize_check(outcome)
    assert summary.level == "error"
    assert summary.title == "Alguma verificação falhou"


def test_collect_dashboard_uses_status_sources(tmp_path):
    env = build_environment(tmp_path)
    env.registry.set(HKLM, PROCESSOR_KEY, "ProcessorNameString", "CPU de teste")
    data = model.collect_dashboard(
        env.services,
        env.config,
        env.paths,
        memory_status=lambda: MemoryStatus(8 * GB, 2 * GB, 8 * GB),
        cpu_count=lambda: 4,
    )
    assert data.info.hostname == "TI-04"
    assert data.processor == "CPU de teste"
    assert data.logical_processors == 4
    assert data.memory.used_percent == pytest.approx(75.0)
    assert data.winget.version == "1.29.380"
    assert data.chrome.installed and data.chrome.version == "153.0.8010.53"
    assert not data.reboot.required
    assert data.disk.status is Status.SUCCESS
    assert data.last_run is None
    assert not env.paths.history_db.exists()  # somente leitura

    run_maintenance(env.config, env.paths, RunOptions(console=False), env.services)
    data = model.collect_dashboard(env.services, env.config, env.paths)
    assert data.last_run is not None and data.last_run.result == "SUCCESS_WITH_WARNINGS"
    assert model.last_run_label(data.last_run).endswith("Sucesso com pendências")


def test_assess_dashboard(tmp_path):
    env = build_environment(tmp_path)
    data = model.collect_dashboard(env.services, env.config, env.paths)
    assessment = model.assess_dashboard(data)
    assert (assessment.level, assessment.title, assessment.items) == (
        "ok",
        "Nenhum problema detectado",
        [],
    )

    from dataclasses import replace

    pending = replace(
        data,
        reboot=RebootStatus(True, (SOURCE_CBS,)),
        winget=None,
        disk=ModuleResult("disk", Status.WARNING, "Pouco espaço livre em C: (5.0 GB livres, 2%)"),
    )
    assessment = model.assess_dashboard(pending)
    assert assessment.level == "warning"
    assert assessment.title == "3 pontos de atenção"
    assert "componentes do Windows" in assessment.items[0][1]

    errored = model.assess_dashboard(data, config_error="'x' deve ser true ou false.")
    assert errored.level == "error" and errored.title == "Erro crítico"
    assert model.assess_dashboard(None).level == "ok"


def test_failed_last_run_needs_attention(tmp_path):
    env = build_environment(tmp_path)
    store = HistoryStore(env.paths.history_db)
    store.initialize()
    run_id = store.start_run(
        hostname="TI-04",
        started_at=env.services.now(),
        tasks=["chrome"],
        dry_run=False,
        agent_version="1.0.0",
        log_file=None,
    )
    store.finish_run(
        run_id,
        finished_at=env.services.now(),
        duration_seconds=1,
        result=OverallResult.FAILED.value,
        chrome_updated=False,
        windows_updates_installed=0,
        cleanup_performed=False,
        bytes_removed=0,
        reboot_required=False,
        report_file=None,
    )
    data = model.collect_dashboard(env.services, env.config, env.paths)
    items = model.assess_dashboard(data).items
    assert items == [("warning", "A última manutenção terminou com falha. Consulte o Histórico.")]


def test_load_report_and_modules(tmp_path):
    env = build_environment(tmp_path)
    outcome = run_maintenance(env.config, env.paths, RunOptions(console=False), env.services)
    entry = HistoryStore(env.paths.history_db).get_run(outcome.run_id)
    report = model.load_report(entry)
    assert report["result"] == "SUCCESS_WITH_WARNINGS"
    modules = model.report_modules(report)
    assert [m.module for m in modules] == [m.module for m in outcome.report.modules]
    assert all(isinstance(m.status, Status) for m in modules)

    outcome.json_file.write_text("{inválido", encoding="utf-8")
    assert model.load_report(entry) is None
    outcome.json_file.write_text(json.dumps({"modules": [{"status": "???"}, 3]}), encoding="utf-8")
    assert model.report_modules(model.load_report(entry)) == []


def test_history_texts():
    assert texts.history_result("SUCCESS") == ("Sucesso", "ok")
    assert texts.history_result(RESULT_RUNNING) == ("Em andamento", "info")
    assert texts.history_result(RESULT_INTERRUPTED) == ("Interrompida", "error")
    assert texts.task_list("chrome,cleanup") == "Google Chrome, Limpeza de temporários"
    assert texts.task_list("") == "Nenhuma"
    assert texts.sentence("componentes do Windows") == "Componentes do Windows"
    assert texts.plural(1, "item", "itens") == "1 item"
    assert texts.plural(2, "item", "itens") == "2 itens"


def test_every_module_and_level_has_a_label():
    for module in ("privileges", "winget", "chrome", "windows_update", "disk", "cleanup", "reboot"):
        assert module in texts.MODULE_TITLES
    for level in ("ok", "warning", "error", "info", "skipped"):
        assert level in texts.LEVEL_LABELS
    for overall in OverallResult:
        assert overall in texts.OVERALL_TITLES and overall in texts.OVERALL_LEVELS


def test_manual_update_only_is_recommended(tmp_path):
    env = build_environment(tmp_path, wua_updates=[FakeUpdate("Com licença (KB9)", eula=False)])
    outcome = run_check(env.config, env.paths, tasks=("windows_update",), services=env.services)
    result = next(r for r in outcome.results if r.module == "windows_update")
    recommendation = model.recommend(result, is_admin=True)
    assert recommendation.task is None
    assert "Configurações > Windows Update" in recommendation.text
