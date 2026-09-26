from maintenance.privileges import (
    ELEVATION_DEFAULT,
    ELEVATION_FULL,
    ELEVATION_LIMITED,
    ELEVATION_UNKNOWN,
    PrivilegeInfo,
    get_privilege_info,
    privilege_result,
)
from maintenance.results import Status
from tests.conftest import windows_only


def test_admin_label_and_result():
    info = PrivilegeInfo(is_admin=True, elevation_type=ELEVATION_FULL)
    assert info.label() == "Sim"
    assert not info.can_elevate
    assert privilege_result(info).status is Status.SUCCESS


def test_standard_user_is_warning_without_elevation_hint():
    info = PrivilegeInfo(is_admin=False, elevation_type=ELEVATION_DEFAULT)
    assert info.label() == "Não"
    result = privilege_result(info)
    assert result.status is Status.WARNING
    assert "sem privilégios administrativos" in result.summary
    assert "Executar como administrador" not in result.summary


def test_admin_without_elevation_gets_hint():
    info = PrivilegeInfo(is_admin=False, elevation_type=ELEVATION_LIMITED)
    assert info.can_elevate
    assert "Executar como administrador" in info.label()
    assert "Executar como administrador" in privilege_result(info).summary


@windows_only
def test_real_privilege_detection_returns_consistent_values():
    info = get_privilege_info()
    assert isinstance(info.is_admin, bool)
    assert info.elevation_type in {
        ELEVATION_DEFAULT,
        ELEVATION_FULL,
        ELEVATION_LIMITED,
        ELEVATION_UNKNOWN,
    }
    if info.elevation_type == ELEVATION_FULL:
        assert info.is_admin
