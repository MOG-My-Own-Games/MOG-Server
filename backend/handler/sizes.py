"""What a game takes on the server's disk: its folder in the library (the installers), the install
caches built from it, and the saves kept for it.

Walking a big folder is slow, so the result is remembered until the top level of the folder or of one
of its caches looks different (names, sizes and times of the entries directly inside), or for
`MAX_AGE` seconds, which covers a change deeper down. Saves come from the database and are always live."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from models.game import Game
from models.install_session import ACTIVE_INSTALL_STATES
from utils.install_cache import dir_size_bytes, session_cache_dir

from handler.database import db_game_handler, db_install_session_handler, db_saves_handler
from handler.filesystem import fs_game_handler

MAX_AGE = 30 * 60
# While an install of the game runs its cache grows below the top level, which the fingerprint cannot see.
BUSY_MAX_AGE = 20


@dataclass(frozen=True)
class GameSizes:
    installer: int
    cache: int
    saves: int
    files: int = 0  # files in the game's folder

    @property
    def total(self) -> int:
        return self.installer + self.cache + self.saves


@dataclass
class _Remembered:
    taken: float
    fingerprint: tuple
    installer: int
    cache: int
    files: int


_remembered: dict[int, _Remembered] = {}
_lock = threading.Lock()


def _top_level(path: Path) -> tuple:
    """Name, size and time of what is directly in `path` (or of `path` itself when it is a file)."""
    try:
        if path.is_file():
            st = path.stat()
            return ((path.name, st.st_size, st.st_mtime_ns),)
        with os.scandir(path) as entries:
            found = []
            for entry in entries:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                found.append((entry.name, 0 if entry.is_dir() else st.st_size, st.st_mtime_ns))
            return tuple(sorted(found))
    except OSError:
        return ()


def _fingerprint(root: Path, cache_dirs: list[Path]) -> tuple:
    return (_top_level(root), tuple((d.name, _top_level(d)) for d in cache_dirs))


def _sessions(game_id: int) -> list:
    return db_install_session_handler.get_sessions_for_game(game_id)


def _cache_dirs(sessions: list) -> list[Path]:
    dirs = (session_cache_dir(s.id) for s in sessions)
    return sorted((d for d in dirs if d.is_dir()), key=lambda d: d.name)


def forget(game_id: int | None = None) -> None:
    """Drop what is remembered for a game, or for all of them."""
    with _lock:
        if game_id is None:
            _remembered.clear()
        else:
            _remembered.pop(game_id, None)


def game_sizes(game: Game) -> GameSizes:
    root = fs_game_handler.get_game_root_abs_path(game)
    sessions = _sessions(game.id)
    cache_dirs = _cache_dirs(sessions)
    fingerprint = _fingerprint(root, cache_dirs)
    max_age = BUSY_MAX_AGE if any(s.state in ACTIVE_INSTALL_STATES for s in sessions) else MAX_AGE
    with _lock:
        known = _remembered.get(game.id)
    if known is None or known.fingerprint != fingerprint or time.monotonic() - known.taken > max_age:
        listed = fs_game_handler.list_game_files_flat(game)
        cache = sum(dir_size_bytes(d) for d in cache_dirs)
        known = _Remembered(time.monotonic(), fingerprint, sum(f.size_bytes for f in listed), cache, len(listed))
        with _lock:
            _remembered[game.id] = known
        if game.size_bytes != known.installer:
            db_game_handler.update_game(game.id, {"size_bytes": known.installer})
    _versions, saves = db_saves_handler.summary(game_id=game.id)
    return GameSizes(known.installer, known.cache, saves, known.files)


def game_sizes_by_id(game_id: int) -> GameSizes | None:
    game = db_game_handler.get_game(game_id)
    return game_sizes(game) if game else None
