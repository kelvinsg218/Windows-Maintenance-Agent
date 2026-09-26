"""Testes da limpeza em diretórios temporários controlados (nunca no %TEMP% real)."""

import os
import stat
import time
from pathlib import Path

import pytest

from maintenance.cleanup import (
    PROFILE_LIST_KEY,
    TARGET_ALL_PROFILES_TEMP,
    TARGET_USER_TEMP,
    TARGET_WINDOWS_TEMP,
    UnsafePathError,
    clean_tree,
    plan_targets,
    process_cleanup,
    validate_root,
)
from maintenance.results import Status
from tests.conftest import FakeRegistry, windows_only

DAY = 86400
MIN_AGE = 7 * DAY
# "Agora" 30 dias no futuro: arquivos criados durante o teste parecem antigos.
# Para simular um arquivo recente, sua data de modificação é movida para perto de NOW.
NOW = time.time() + 30 * DAY


def make_file(path: Path, size: int = 100, *, recent: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if recent:
        future = NOW - DAY
        os.utime(path, (future, future))
    return path


def create_junction(link: Path, target: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("CreateJunction indisponível")
    winapi.CreateJunction(str(target), str(link))


@pytest.fixture
def machine(tmp_path):
    """Estrutura falsa: <tmp>/Windows/Temp e <tmp>/Users/<perfil>/AppData/Local/Temp."""
    windows = tmp_path / "Windows"
    (windows / "Temp").mkdir(parents=True)
    users = tmp_path / "Users"
    registry = FakeRegistry()
    registry.set("HKLM", PROFILE_LIST_KEY, "ProfilesDirectory", str(users))
    profiles = {}
    for index, name in enumerate(("alice", "bob")):
        profile = users / name
        (profile / "AppData" / "Local" / "Temp").mkdir(parents=True)
        sid = f"S-1-5-21-1000-1000-1000-{1000 + index}"
        registry.set("HKLM", rf"{PROFILE_LIST_KEY}\{sid}", "ProfileImagePath", str(profile))
        profiles[name] = profile
    registry.set("HKLM", rf"{PROFILE_LIST_KEY}\S-1-5-18", "ProfileImagePath", str(windows))
    return {"windows": windows, "users": users, "registry": registry, "profiles": profiles}


def test_old_files_removed_recent_kept_and_root_preserved(tmp_path):
    root = tmp_path / "Temp"
    old = make_file(root / "old.tmp", 1000)
    nested = make_file(root / "installer" / "sub" / "old.bin", 500)
    recent = make_file(root / "recent.tmp", 50, recent=True)
    stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=False, now=NOW)
    assert not old.exists() and not nested.exists()
    assert recent.exists()
    assert not (root / "installer").exists()  # pastas vazias antigas removidas
    assert root.is_dir()
    assert stats.files_scanned == 3
    assert stats.files_removed == 2
    assert stats.bytes_removed == 1500
    assert stats.skipped_recent == 1 and stats.files_skipped == 1
    assert stats.dirs_removed == 2


def test_dry_run_removes_nothing(tmp_path):
    root = tmp_path / "Temp"
    old = make_file(root / "old.tmp", 2048)
    stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=True, now=NOW)
    assert old.exists()
    assert stats.files_eligible == 1 and stats.bytes_eligible == 2048
    assert stats.files_removed == 0 and stats.bytes_removed == 0


def test_recently_created_file_with_old_modification_date_is_kept(tmp_path):
    """Arquivos recém-extraídos preservam a data de modificação original (antiga)."""
    root = tmp_path / "Temp"
    extracted = make_file(root / "setup" / "payload.dll")
    long_ago = time.time() - 365 * DAY
    os.utime(extracted, (long_ago, long_ago))
    stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=False, now=time.time())
    assert extracted.exists()
    assert stats.skipped_recent == 1


@windows_only
def test_locked_file_is_skipped(tmp_path):
    root = tmp_path / "Temp"
    locked = make_file(root / "in-use.tmp")
    with open(locked, "rb"):
        stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=False, now=NOW)
    assert locked.exists()
    assert stats.skipped_in_use == 1 and stats.files_removed == 0


def test_read_only_file_is_removed(tmp_path):
    root = tmp_path / "Temp"
    readonly = make_file(root / "readonly.tmp")
    os.chmod(readonly, stat.S_IREAD)
    stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=False, now=NOW)
    assert not readonly.exists()
    assert stats.files_removed == 1


@windows_only
def test_junction_is_never_followed_nor_removed(tmp_path):
    outside = tmp_path / "Documents"
    precious = make_file(outside / "important.docx")
    root = tmp_path / "Temp"
    root.mkdir()
    create_junction(root / "link", outside)
    stats = clean_tree(root, min_age_seconds=MIN_AGE, dry_run=False, now=NOW)
    assert precious.exists()
    assert (root / "link").exists()
    assert stats.skipped_links == 1
    assert stats.files_scanned == 0


def test_validate_root_accepts_only_expected_layouts(machine):
    windows, users = machine["windows"], machine["users"]
    alice_temp = users / "alice" / "AppData" / "Local" / "Temp"
    assert validate_root(windows / "Temp", windows_dir=windows, profiles_dir=users)
    assert validate_root(alice_temp, windows_dir=windows, profiles_dir=users)

    documents = users / "alice" / "Documents"
    documents.mkdir()
    deep = alice_temp / "sub"
    deep.mkdir()
    fake_temp = users / "alice" / "Temp"
    fake_temp.mkdir()
    for bad in (documents, deep, fake_temp, windows, users, users / "alice"):
        with pytest.raises(UnsafePathError):
            validate_root(bad, windows_dir=windows, profiles_dir=users)
    with pytest.raises(UnsafePathError, match="inexistente"):
        validate_root(
            users / "carol" / "AppData" / "Local" / "Temp", windows_dir=windows, profiles_dir=users
        )


@windows_only
def test_validate_root_rejects_junction_root(machine, tmp_path):
    users = machine["users"]
    target = tmp_path / "elsewhere"
    target.mkdir()
    link_parent = users / "mallory" / "AppData" / "Local"
    link_parent.mkdir(parents=True)
    create_junction(link_parent / "Temp", target)
    with pytest.raises(UnsafePathError, match="link"):
        validate_root(link_parent / "Temp", windows_dir=machine["windows"], profiles_dir=users)


def test_plan_without_admin_uses_only_current_user_temp(machine):
    env = {"USERPROFILE": str(machine["profiles"]["alice"])}
    plan = plan_targets(
        [TARGET_USER_TEMP, TARGET_WINDOWS_TEMP, TARGET_ALL_PROFILES_TEMP],
        is_admin=False,
        registry=machine["registry"],
        env=env,
        windows_dir=machine["windows"],
    )
    assert [target.name for target in plan.targets] == [TARGET_USER_TEMP]
    assert len(plan.notes) == 2 and all("administrativos" in note for note in plan.notes)


def test_plan_ignores_manipulated_temp_variable(machine, tmp_path):
    """%TEMP% não é usado: mesmo apontando para Documentos, a raiz vem do perfil."""
    documents = tmp_path / "Documents"
    documents.mkdir()
    env = {"USERPROFILE": str(machine["profiles"]["alice"]), "TEMP": str(documents)}
    plan = plan_targets(
        [TARGET_USER_TEMP],
        is_admin=False,
        registry=machine["registry"],
        env=env,
        windows_dir=machine["windows"],
    )
    assert plan.targets[0].paths[0].parts[-4:] == ("alice", "AppData", "Local", "Temp")


def test_plan_as_admin_covers_all_profiles_without_duplicates(machine):
    env = {"USERPROFILE": str(machine["profiles"]["alice"])}
    plan = plan_targets(
        [TARGET_USER_TEMP, TARGET_WINDOWS_TEMP, TARGET_ALL_PROFILES_TEMP],
        is_admin=True,
        registry=machine["registry"],
        env=env,
        windows_dir=machine["windows"],
    )
    by_name = {target.name: target for target in plan.targets}
    assert set(by_name) == {TARGET_USER_TEMP, TARGET_WINDOWS_TEMP, TARGET_ALL_PROFILES_TEMP}
    all_profiles = by_name[TARGET_ALL_PROFILES_TEMP].paths
    assert len(all_profiles) == 1 and "bob" in str(all_profiles[0])  # alice já incluída
    assert plan.warnings == []


def test_plan_for_system_account_skips_user_temp(machine):
    env = {"USERPROFILE": str(machine["windows"] / "System32" / "config" / "systemprofile")}
    plan = plan_targets(
        [TARGET_USER_TEMP],
        is_admin=True,
        registry=machine["registry"],
        env=env,
        windows_dir=machine["windows"],
    )
    assert plan.targets == []
    assert "SYSTEM" in plan.notes[0]


def test_process_cleanup_check_run_and_dry_run(machine):
    env = {"USERPROFILE": str(machine["profiles"]["alice"])}
    temp = machine["profiles"]["alice"] / "AppData" / "Local" / "Temp"
    old = make_file(temp / "old.tmp", 4096)
    options = {
        "is_admin": False,
        "registry": machine["registry"],
        "env": env,
        "windows_dir": machine["windows"],
        "now": NOW,
    }

    check = process_cleanup([TARGET_USER_TEMP], apply=False, **options)
    assert check.status is Status.SUCCESS
    assert check.summary == "4.0 KB de arquivos temporários removíveis"
    assert check.details["bytes_eligible"] == 4096 and old.exists()

    simulated = process_cleanup([TARGET_USER_TEMP], apply=True, dry_run=True, **options)
    assert simulated.status is Status.SKIPPED
    assert "dry-run" in simulated.summary and old.exists()
    assert simulated.details["cleanup_performed"] is False

    done = process_cleanup([TARGET_USER_TEMP], apply=True, **options)
    assert done.status is Status.SUCCESS
    assert done.summary == "4.0 KB removidos"
    assert done.details["files_removed"] == 1 and done.details["cleanup_performed"] is True
    assert not old.exists()


def test_process_cleanup_large_temp_is_warning_in_check(machine, monkeypatch):
    monkeypatch.setattr("maintenance.cleanup.LARGE_TEMP_BYTES", 1000)
    temp = machine["profiles"]["alice"] / "AppData" / "Local" / "Temp"
    make_file(temp / "big.tmp", 2000)
    result = process_cleanup(
        [TARGET_USER_TEMP],
        apply=False,
        is_admin=False,
        registry=machine["registry"],
        env={"USERPROFILE": str(machine["profiles"]["alice"])},
        windows_dir=machine["windows"],
        now=NOW,
    )
    assert result.status is Status.WARNING
    assert "arquivos temporários encontrados" in result.summary


def test_process_cleanup_without_locations(machine):
    result = process_cleanup(
        [TARGET_WINDOWS_TEMP],
        apply=True,
        is_admin=False,
        registry=machine["registry"],
        env={},
        windows_dir=machine["windows"],
    )
    assert result.status is Status.SKIPPED
    assert "requer privilégios administrativos" in result.summary
