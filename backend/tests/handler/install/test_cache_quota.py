"""The limit on the installer caches: what an install does when its installer does not fit."""

import os
from types import SimpleNamespace

import pytest

from handler.database import db_install_session_handler as sessions
from handler.install import cache_quota, runner
from handler.install.cache_quota import CacheTooSmall, Verdict, Wait, decide, room_after
from models.install_session import InstallSession, InstallSessionState

S = InstallSessionState
GB = 1024**3


# --- the decision, with no disk or database ----------------------------------------------------------


def test_the_room_is_the_smaller_of_what_is_left_of_the_limit_and_of_the_disk():
    assert room_after(limit=100, used=40, disk_free=500) == 60
    assert room_after(limit=100, used=40, disk_free=25) == 25
    assert room_after(limit=0, used=40, disk_free=25) == 25  # no limit: the disk alone
    assert room_after(limit=100, used=120, disk_free=500) == 0  # over the limit already
    assert room_after(limit=100, used=40, disk_free=25, released=30) == 55


def test_an_installer_that_fits_just_starts():
    assert decide(need=50, limit=100, used=40, locked=0, disk_free=900, clean=True) is Verdict.FITS


def test_one_that_does_not_fit_makes_the_old_caches_go_when_auto_clean_is_on():
    assert decide(need=80, limit=100, used=90, locked=0, disk_free=900, clean=True) is Verdict.EVICT


def test_with_auto_clean_off_nothing_is_deleted_and_it_is_refused_when_no_install_is_running():
    assert decide(need=80, limit=100, used=90, locked=0, disk_free=900, clean=False) is Verdict.TOO_BIG


def test_when_only_a_running_installs_cache_is_in_the_way_it_waits():
    assert decide(need=80, limit=100, used=90, locked=85, disk_free=900, clean=True) is Verdict.WAIT
    assert decide(need=80, limit=100, used=90, locked=85, disk_free=900, clean=False) is Verdict.WAIT


def test_an_installer_bigger_than_the_limit_is_refused_whatever_runs():
    assert decide(need=150, limit=100, used=10, locked=10, disk_free=900, clean=True) is Verdict.TOO_BIG


def test_the_disk_can_be_the_one_that_is_short_even_with_no_limit():
    assert decide(need=80, limit=0, used=10, locked=0, disk_free=50, clean=True) is Verdict.TOO_BIG


def test_the_own_cache_of_the_session_is_never_counted_as_something_to_wait_for_or_delete():
    """A reinstall over its own finished cache must not wait for itself to end."""
    assert decide(need=80, limit=100, used=90, locked=0, disk_free=900, clean=True, pinned=90) is Verdict.TOO_BIG


@pytest.mark.parametrize("need, room, tight", [(100, 120, False), (100, 119, True), (100, 50, True)])
def test_an_installer_with_less_than_20_percent_to_spare_is_flagged(need, room, tight):
    assert cache_quota.is_tight(need, room) is tight


# --- with caches on a disk and sessions in a database -------------------------------------------------


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    import utils.install_cache as install_cache

    root = tmp_path / "installs"
    root.mkdir()
    monkeypatch.setattr(install_cache, "INSTALL_CACHE_PATH", str(root))
    settings = SimpleNamespace(install_cache_max_bytes=100, install_cache_auto_clean=True)
    monkeypatch.setattr(cache_quota.db_settings_handler, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_quota, "disk_free_bytes", lambda: 10**12)
    need = {"bytes": 50}
    monkeypatch.setattr(cache_quota, "estimate_installer_bytes", lambda session: need["bytes"])
    monkeypatch.setattr(runner, "enqueue_install", lambda session_id: None)
    monkeypatch.setattr(runner, "INSTALL_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(runner.cache_quota, "run_warning", lambda session, warning: None)

    def add(state, size, age):
        """A session in `state` whose cache holds `size` bytes, last touched `age` seconds after the epoch."""
        sid = sessions.add_session(InstallSession(game_id=1, user_id=1, state=state)).id
        if size:
            folder = root / str(sid)
            folder.mkdir()
            (folder / "data").write_bytes(b"x" * size)
        sessions.update_session(sid, {"updated_at": __import__("datetime").datetime.fromtimestamp(age, __import__("datetime").timezone.utc)})
        return sid

    def new(state=S.DETECTING):
        return sessions.add_session(InstallSession(game_id=1, user_id=1, state=state)).id

    return SimpleNamespace(root=root, settings=settings, need=need, add=add, new=new)


def _exists(world, sid):
    return (world.root / str(sid)).is_dir()


def test_the_oldest_caches_not_in_use_are_deleted_first_and_only_as_many_as_needed(world):
    oldest, middle, newest = world.add(S.DONE, 30, 1000), world.add(S.DONE, 30, 2000), world.add(S.DONE, 30, 3000)
    world.need["bytes"] = 50  # 100 limit, 90 used: 10 free

    warning = cache_quota.admit(sessions.get_session(world.new()))

    assert not _exists(world, oldest) and not _exists(world, middle) and _exists(world, newest)  # 10 + 30 is not enough for 50, 10 + 30 + 30 is
    assert sessions.get_session(oldest).state is S.EXPIRED
    assert "2 older install caches" in warning


def test_a_cache_with_an_install_in_progress_is_never_deleted(world):
    running = world.add(S.INSTALLING, 60, 1)  # the oldest of all
    idle = world.add(S.DONE, 30, 5000)
    world.need["bytes"] = 35  # 100 limit, 90 used: 10 free, 40 once the idle one is gone

    cache_quota.admit(sessions.get_session(world.new()))

    assert _exists(world, running) and not _exists(world, idle)


def test_with_auto_clean_off_a_cache_is_not_deleted_and_the_installer_is_refused(world):
    old = world.add(S.DONE, 90, 1000)
    world.settings.install_cache_auto_clean = False
    world.need["bytes"] = 40

    with pytest.raises(CacheTooSmall) as error:
        cache_quota.admit(sessions.get_session(world.new()))

    assert _exists(world, old) and "automatic cleaning" in str(error.value)


def test_an_installer_bigger_than_the_whole_cache_is_refused_with_the_sizes_in_the_message(world):
    world.need["bytes"] = 200
    with pytest.raises(CacheTooSmall) as error:
        cache_quota.admit(sessions.get_session(world.new()))
    assert "does not fit" in str(error.value)


def test_a_tight_fit_is_a_warning_and_a_comfortable_one_says_nothing(world):
    world.need["bytes"] = 90  # 100 free, 90 * 1.2 > 100
    assert "tight" in cache_quota.admit(sessions.get_session(world.new()))
    world.need["bytes"] = 50
    assert cache_quota.admit(sessions.get_session(world.new())) is None


def test_an_installer_whose_size_is_not_known_is_let_through(world, monkeypatch):
    monkeypatch.setattr(cache_quota, "estimate_installer_bytes", lambda session: None)
    assert cache_quota.admit(sessions.get_session(world.new())) is None


def test_a_session_waits_in_the_queue_while_a_running_install_holds_the_room_and_starts_when_it_ends(world):
    running = world.add(S.INSTALLING, 80, 1)
    world.need["bytes"] = 60
    waiting = world.new()

    assert runner.start_or_queue(waiting) is False

    row = sessions.get_session(waiting)
    assert row.state is S.QUEUED and "Waiting for cache space" in row.warning and "perhaps recent" in row.warning
    assert runner.dispatch_queue() == 0 and sessions.get_session(waiting).state is S.QUEUED  # still held

    sessions.update_session(running, {"state": S.DONE})
    assert runner.dispatch_queue() == 1
    row = sessions.get_session(waiting)
    assert row.state is S.INSTALLING and not _exists(world, running) and "deleted" in row.warning


def test_a_waiting_session_whose_installer_can_no_longer_fit_fails_with_the_reason(world):
    running = world.add(S.INSTALLING, 80, 1)
    world.need["bytes"] = 60
    waiting = world.new()
    runner.start_or_queue(waiting)

    world.settings.install_cache_max_bytes = 30  # the limit was lowered meanwhile
    sessions.update_session(running, {"state": S.DONE})
    runner.dispatch_queue()

    row = sessions.get_session(waiting)
    assert row.state is S.FAILED and "does not fit" in row.error


def test_starting_with_an_installer_that_can_never_fit_raises_for_the_endpoint_to_refuse(world):
    world.need["bytes"] = 500
    with pytest.raises(CacheTooSmall):
        runner.start_or_queue(world.new())


def test_what_the_web_ui_shows_is_used_limit_and_the_smaller_free_space(world, monkeypatch):
    world.add(S.DONE, 30, 1)
    assert cache_quota.snapshot() == {
        "used_bytes": 30, "limit_bytes": 100, "free_bytes": 70, "disk_free_bytes": 10**12, "auto_clean": True,
    }
    monkeypatch.setattr(cache_quota, "disk_free_bytes", lambda: 20)
    assert cache_quota.snapshot()["free_bytes"] == 20
    world.settings.install_cache_max_bytes = None
    assert cache_quota.snapshot()["limit_bytes"] == 0 and cache_quota.snapshot()["free_bytes"] == 20
