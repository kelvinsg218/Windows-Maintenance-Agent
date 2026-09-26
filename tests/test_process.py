import sys
import time

import pytest

from maintenance.process import (
    OperationTimeoutError,
    call_with_timeout,
    format_exit_code,
    run_command,
)

PYTHON = sys.executable


def test_captures_stdout_stderr_exit_code_and_duration():
    result = run_command(
        [PYTHON, "-c", "import sys; print('olá'); print('erro', file=sys.stderr); sys.exit(3)"],
        timeout=30,
    )
    assert result.exit_code == 3
    assert "olá" in result.stdout
    assert "erro" in result.stderr
    assert result.duration_seconds > 0
    assert not result.ok


def test_success_is_ok():
    result = run_command([PYTHON, "-c", "pass"], timeout=30)
    assert result.ok and result.exit_code == 0


def test_arguments_are_not_interpreted_by_a_shell():
    payload = "a & echo INJECTED | calc"
    result = run_command([PYTHON, "-c", "import sys; print(sys.argv[1])", payload], timeout=30)
    assert result.stdout.strip() == payload


def test_timeout_kills_process():
    result = run_command([PYTHON, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert result.timed_out
    assert result.exit_code is None
    assert result.duration_seconds < 20
    assert "tempo limite" in result.describe_exit()


def test_missing_executable_is_reported_not_raised():
    result = run_command(["C:\\nao\\existe\\programa.exe"], timeout=5)
    assert result.error
    assert not result.ok
    assert "falha ao iniciar" in result.describe_exit()


def test_negative_exit_codes_are_normalized_to_unsigned_hresult():
    result = run_command([PYTHON, "-c", "import sys; sys.exit(-1978335212)"], timeout=30)
    assert result.exit_code == 0x8A150014
    assert format_exit_code(result.exit_code) == "0x8A150014"
    assert format_exit_code(1) == "1"
    assert format_exit_code(None) == "-"


def test_empty_arguments_are_rejected():
    with pytest.raises(ValueError):
        run_command([], timeout=1)


def test_call_with_timeout_returns_value_and_propagates_errors():
    assert call_with_timeout(lambda: 42, 5) == 42

    def boom():
        raise RuntimeError("falhou")

    with pytest.raises(RuntimeError, match="falhou"):
        call_with_timeout(boom, 5)


def test_call_with_timeout_abandons_slow_operation():
    started = time.monotonic()
    with pytest.raises(OperationTimeoutError):
        call_with_timeout(lambda: time.sleep(5), 0.3, name="lenta")
    assert time.monotonic() - started < 3
