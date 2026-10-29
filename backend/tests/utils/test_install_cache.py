import os
import shutil
from types import SimpleNamespace

import pytest

import handler.database
from utils import install_cache
from models.install_session import InstallSessionState


class FakeSessions:
    """The session store, with just what the cache cleanup asks of it."""

    def __init__(self, sessions=(), expired=()):
        self.rows = {s.id: s for s in sessions}
        self.expired = list(expired)
        self.updates = []

    def get_session(self, session_id):
        return self.rows.get(session_id)

    def get_expired_sessions(self):
        return self.expired

    def update_session(self, session_id, values):
        self.updates.append((session_id, values))

    def delete_session(self, session_id):
        self.rows.pop(session_id, None)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(install_cache, "INSTALL_CACHE_PATH", str(tmp_path))
    return tmp_path


@pytest.fixture
def sessions(monkeypatch):
    store = FakeSessions()
    monkeypatch.setattr(handler.database, "db_install_session_handler", store)
    return store


def _cache(root, session_id, age_seconds=0):
    folder = root / str(session_id)
    (folder / "prefix").mkdir(parents=True)
    (folder / "prefix" / "file").write_bytes(b"x")
    if age_seconds:
        old = folder.stat().st_mtime - age_seconds
        os.utime(folder, (old, old))
    return folder


def _cannot_remove(monkeypatch, *kept):
    """A cache the server may not delete (made as root): the removal leaves these session folders where they are."""
    real = shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if not any(str(path).endswith(f"/{n}") for n in kept):
            real(path, *args, **kwargs)

    monkeypatch.setattr(install_cache.shutil, "rmtree", rmtree)


def test_a_cache_is_removed_and_a_missing_one_is_not_an_error(root):
    folder = _cache(root, 5)

    assert install_cache.clear_session_cache(5) is True
    assert not folder.exists() and not install_cache.cache_left(5)
    assert install_cache.clear_session_cache(5) is False


def test_a_cache_that_could_not_be_removed_is_still_there_for_the_callers_to_see(root, monkeypatch):
    """The "?" cache of the Settings list: the server runs as `mog`, the files of an older install were root's, so the delete
    removed nothing, said nothing and went on to forget the session."""
    _cache(root, 68)
    _cannot_remove(monkeypatch, 68)

    install_cache.clear_session_cache(68)

    assert install_cache.cache_left(68)


def test_an_expired_cache_is_cleared_and_marked_expired(root, sessions):
    _cache(root, 26)
    sessions.expired = [SimpleNamespace(id=26)]

    assert install_cache.cleanup_expired_installs() == 1

    assert sessions.updates == [(26, {"state": InstallSessionState.EXPIRED})]
    assert not install_cache.cache_left(26)


def test_an_expired_cache_that_could_not_be_removed_is_not_marked_expired_and_the_rest_go_on(root, sessions, monkeypatch):
    _cache(root, 26)
    _cache(root, 31)
    sessions.expired = [SimpleNamespace(id=26), SimpleNamespace(id=31)]
    _cannot_remove(monkeypatch, 26)

    assert install_cache.cleanup_expired_installs() == 1

    assert sessions.updates == [(31, {"state": InstallSessionState.EXPIRED})]  # 26 is looked at again next time


def test_the_folders_no_session_owns_are_removed_once_they_are_old_enough(root, sessions):
    _cache(root, 68, age_seconds=3 * 86400)  # no session: the "?" row
    _cache(root, 69, age_seconds=3 * 86400)
    _cache(root, 70)  # young: its session may not be saved yet
    (root / "notes").mkdir()
    sessions.rows[69] = SimpleNamespace(id=69)

    assert install_cache.remove_orphan_caches() == 1

    assert not install_cache.cache_left(68)
    assert install_cache.cache_left(69) and install_cache.cache_left(70) and (root / "notes").exists()


def test_a_sweep_does_both_and_counts_what_it_freed(root, sessions):
    _cache(root, 26)
    _cache(root, 68, age_seconds=3 * 86400)
    sessions.rows[26] = SimpleNamespace(id=26)
    sessions.expired = [SimpleNamespace(id=26)]

    assert install_cache.sweep_install_cache() == 2


def test_clearing_a_session_whose_files_stay_keeps_the_session_when_a_game_is_purged(root, monkeypatch):
    """purge_superseded_sessions forgets the other sessions of a game: one whose files stayed would become a "?" cache."""
    store = FakeSessions()
    store.rows = {7: SimpleNamespace(id=7, state=InstallSessionState.DONE, user_id=1)}
    store.get_sessions_for_game = lambda game_id: list(store.rows.values())
    monkeypatch.setattr(handler.database, "db_install_session_handler", store)
    monkeypatch.setattr(install_cache, "INSTALL_CACHE_PATH", str(root))
    _cache(root, 7)
    _cannot_remove(monkeypatch, 7)

    assert install_cache.purge_superseded_sessions(1, keep_session_id=8, owner_user_id=1) == 0

    assert 7 in store.rows
