import pytest

from handler.install import runner
from handler.install.runner import EARLY_STOP_BYTES, InstallStoppedEarly, check_not_stopped_early


def make(tmp_path, *sizes):
    files = []
    for i, size in enumerate(sizes):
        path = tmp_path / f"f{i}"
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
    check_not_stopped_early(make(tmp_path, EARLY_STOP_BYTES), 1)


def test_a_clean_small_install_is_kept(tmp_path):
    check_not_stopped_early(make(tmp_path, 10), 0)
    check_not_stopped_early(make(tmp_path, 10), None)


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
