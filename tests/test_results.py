from maintenance.results import (
    EXIT_CODES,
    TASKS,
    ModuleResult,
    OverallResult,
    Status,
    aggregate,
)


def result(module: str, status: Status, **details) -> ModuleResult:
    return ModuleResult(module, status, f"{module} {status}", details=details)


def test_all_success_is_success():
    results = [result("system_info", Status.SUCCESS), result("chrome", Status.SUCCESS)]
    assert aggregate(results, requested_tasks=TASKS) is OverallResult.SUCCESS


def test_skipped_tasks_do_not_degrade_result():
    results = [result("chrome", Status.SKIPPED), result("cleanup", Status.SUCCESS)]
    assert aggregate(results, requested_tasks=TASKS) is OverallResult.SUCCESS


def test_optional_task_failure_is_not_total_failure():
    results = [
        result("chrome", Status.FAILED),
        result("windows_update", Status.SUCCESS),
        result("cleanup", Status.SUCCESS),
    ]
    assert aggregate(results, requested_tasks=TASKS) is OverallResult.SUCCESS_WITH_WARNINGS


def test_all_executed_tasks_failed_is_failed():
    results = [
        result("chrome", Status.SKIPPED),
        result("windows_update", Status.FAILED),
        result("cleanup", Status.FAILED),
        result("disk", Status.SUCCESS),
    ]
    assert aggregate(results, requested_tasks=TASKS) is OverallResult.FAILED


def test_single_requested_task_failure_is_failed():
    results = [result("chrome", Status.FAILED), result("disk", Status.SUCCESS)]
    assert aggregate(results, requested_tasks=["chrome"]) is OverallResult.FAILED


def test_warning_in_check_module_gives_warnings():
    results = [result("privileges", Status.WARNING), result("cleanup", Status.SUCCESS)]
    assert aggregate(results, requested_tasks=TASKS) is OverallResult.SUCCESS_WITH_WARNINGS


def test_reboot_required_gives_warnings():
    results = [result("windows_update", Status.SUCCESS)]
    outcome = aggregate(results, requested_tasks=TASKS, reboot_required=True)
    assert outcome is OverallResult.SUCCESS_WITH_WARNINGS


def test_critical_error_is_failed():
    results = [result("chrome", Status.SUCCESS)]
    assert aggregate(results, requested_tasks=TASKS, critical_error=True) is OverallResult.FAILED


def test_tags_and_update_marker():
    assert result("x", Status.SUCCESS).tag == "OK"
    assert result("x", Status.FAILED).tag == "ERROR"
    assert result("x", Status.SKIPPED).tag == "SKIPPED"
    assert result("x", Status.WARNING).tag == "WARNING"
    assert result("x", Status.WARNING, updates_available=2).tag == "UPDATE"


def test_exit_codes_and_serialization():
    assert EXIT_CODES[OverallResult.SUCCESS] == 0
    assert EXIT_CODES[OverallResult.SUCCESS_WITH_WARNINGS] == 1
    assert EXIT_CODES[OverallResult.FAILED] == 2
    data = result("chrome", Status.WARNING, updates_available=1).to_dict()
    assert data["status"] == "WARNING"
    assert data["details"] == {"updates_available": 1}
