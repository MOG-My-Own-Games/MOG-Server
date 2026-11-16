import pytest

from handler.install import runner
from handler.install.runner import EARLY_STOP_BYTES, InstallStoppedEarly, check_not_stopped_early, has_game_program


def make(tmp_path, *sizes):
    files = []
    for i, size in enumerate(sizes):
        path = tmp_path / f"f{i}"
        path.write_bytes(b"x" * size)
        files.append(path)
    return files


def make_named(tmp_path, *entries):
    files = []
    for name, size in entries:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        files.append(path)
    return files


def test_only_empty_files_is_not_an_install_whatever_the_exit_code(tmp_path):
    for code in (None, 0, 253):
        with pytest.raises(InstallStoppedEarly):
            check_not_stopped_early(make(tmp_path, 0), code)


def test_a_failing_exit_with_next_to_nothing_written_is_not_an_install(tmp_path):
    with pytest.raises(InstallStoppedEarly, match="253"):
        check_not_stopped_early(make(tmp_path, 10, 20), 253)


def test_a_failing_exit_after_a_real_install_is_kept(tmp_path):
    check_not_stopped_early(make_named(tmp_path, ("Game/game.exe", EARLY_STOP_BYTES)), 1)


def test_a_big_payload_that_was_never_unpacked_is_not_an_install(tmp_path):
    """Caught live: a setup left its 3.8 GB payload, an uninstaller and a redistributable's setup, then exited with 253 before
    unpacking it. The size check passed and the session was marked done with no program to launch."""
    files = make_named(
        tmp_path,
        ("Game/payload.pack", EARLY_STOP_BYTES * 4),
        ("Game/unins000.exe", 2000),
        ("Game/_Redist/dxwebsetup.exe", 2000),
    )
    with pytest.raises(InstallStoppedEarly, match="no program to launch"):
        check_not_stopped_early(files, 253)


@pytest.mark.parametrize("code", [None, 0, 1])
def test_nothing_to_launch_is_no_install_whatever_the_exit_code(tmp_path, code):
    with pytest.raises(InstallStoppedEarly, match="no program to launch"):
        check_not_stopped_early(make_named(tmp_path, ("Game/data.pack", EARLY_STOP_BYTES * 2)), code)


def test_the_reason_is_short_and_says_what_happened(tmp_path):
    with pytest.raises(InstallStoppedEarly) as empty:
        check_not_stopped_early(make(tmp_path, 0), 253)
    assert empty.value.short == "the installer closed itself before installing anything (exit 253)"

    with pytest.raises(InstallStoppedEarly) as none:
        check_not_stopped_early(make_named(tmp_path, ("Game/data.pack", EARLY_STOP_BYTES * 2)), 0)
    assert none.value.short == "no program to launch in the install"
    assert len(none.value.short) < 80


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Game/game.exe", True),
        ("Game/bin/Launcher.EXE", True),
        ("Game/start.bat", True),
        ("Game/unins000.exe", False),
        ("Game/_Redist/dxwebsetup.exe", False),
        ("Game/redist/vc_redist.x64.exe", False),
        ("Game/data.pack", False),
    ],
)
def test_what_counts_as_the_games_own_program(tmp_path, name, expected):
    assert has_game_program(make_named(tmp_path, (name, 1))) is expected


def test_a_clean_small_install_is_kept(tmp_path):
    check_not_stopped_early(make_named(tmp_path, ("tiny.exe", 10)), 0)
    check_not_stopped_early(make_named(tmp_path, ("tiny.exe", 10)), None)


def test_run_install_repeats_a_run_that_stopped_early(monkeypatch):
    calls = []

    def fake(session_id, attempt=1):
        calls.append(attempt)
        return attempt < 3

    monkeypatch.setattr(runner, "_run_install", fake)
    monkeypatch.setattr(runner, "_log_install_end", lambda _id: None)
    monkeypatch.setattr(runner, "dispatch_queue", lambda: None)
    runner.run_install(1)
    assert calls == [1, 2, 3]


def test_every_repeat_starts_from_an_empty_cache(monkeypatch):
    """A run over the files the stopped one left made an installer stop or skip past them; each repeat needs a clean cache."""
    cleared = []
    monkeypatch.setattr(runner, "_run_install", lambda _id, attempt=1: attempt < 3)
    monkeypatch.setattr(runner, "clear_session_cache", cleared.append)
    monkeypatch.setattr(runner, "_log_install_end", lambda _id: None)
    monkeypatch.setattr(runner, "dispatch_queue", lambda: None)
    runner.run_install(7)
    assert cleared == [7, 7]


def test_disc_games_folder_is_routed_to_the_writable_games_dir(tmp_path):
    """Regression test: an installer defaulting to D:\\Games\\<title> wrote into the read-only disc, so the scan found
    nothing and the install failed after reporting success."""
    runner._route_disc_games_dir(tmp_path)
    assert (tmp_path / "Games").is_symlink()
    assert str((tmp_path / "Games").readlink()) == "/Games"


def test_a_games_folder_already_on_the_disc_is_left_alone(tmp_path):
    (tmp_path / "Games").mkdir()
    runner._route_disc_games_dir(tmp_path)
    assert not (tmp_path / "Games").is_symlink()


@pytest.mark.parametrize(
    ("installed", "installer", "expected"),
    [(10, 100, True), (100, 100, False), (200, 100, False), (10, None, False), (10, 0, False)],
)
def test_smaller_than_installer_only_when_the_installer_size_is_known(installed, installer, expected):
    assert runner.is_smaller_than_installer(installed, installer) is expected


def test_small_install_only_warns_and_never_fails_the_session(monkeypatch):
    warned = []
    monkeypatch.setattr(runner, "notify_install_small", lambda *args: warned.append(args))

    runner._warn_if_smaller_than_installer(4, 10, 100, 1)
    runner._warn_if_smaller_than_installer(4, 500, 100, 0)

    assert warned == [(4, 10, 100, 1)]


def test_a_failed_install_passes_its_short_reason_to_the_notification(monkeypatch):
    sent = []
    monkeypatch.setattr(runner, "db_install_session_handler", type("Db", (), {"update_session": staticmethod(lambda *a, **k: None)}))
    monkeypatch.setattr(runner, "_cancelled", lambda _id: False)
    monkeypatch.setattr(runner, "notify_auto_mode_failed", lambda *args: sent.append(args))

    runner._fail(9, "The installer exited with code 253 ...", "no program to launch in the install")

    assert sent == [(9, "The installer exited with code 253 ...", "no program to launch in the install")]
