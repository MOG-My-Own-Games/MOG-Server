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
