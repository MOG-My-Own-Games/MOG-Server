"""Library scanning: one Game per top-level entry under a Library's root_path.

No platform detection, no ROM hashing - a library is just a folder of
installers/games, and a scan's only job is to keep the `games` table in
sync with what's actually on disk. Metadata (IGDB/SteamGridDB) is matched
separately, not as part of scanning (see docs/TODO.md for auto-matching).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from handler.database import db_game_handler
from handler.filesystem.installer_detection import is_addon_folder, is_base_installer
from models.game import Game
from models.library import Library


@dataclass(frozen=True, slots=True)
class ScanResult:
    added: int
    missing: int
    total: int
    new_games: tuple[tuple[int, str], ...] = ()  # (id, name) of the games this scan added


def has_files(path: Path) -> bool:
    """A file, or a directory with a file anywhere inside: an empty folder is not a game."""
    if not path.is_dir():
        return True
    return any(files for _, _, files in os.walk(path))


def only_addons(path: Path) -> bool:
    """A folder with add-on folders (mods, DLC, ...) and nothing that installs the base game.

    Stops at the first installer of the base game, so an ordinary game costs a glance. The add-on
    folders themselves are not searched: an installer inside one is not the base game's."""
    if not path.is_dir():
        return False
    try:
        if not any(is_addon_folder(e.name) for e in path.iterdir() if e.is_dir()):
            return False
    except OSError:
        return False
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        if here == path:
            dirnames[:] = [d for d in dirnames if not is_addon_folder(d)]
        rel = here.relative_to(path)
        if any(is_base_installer(PurePosixPath(*rel.parts, name).as_posix()) for name in filenames):
            return False
    return True


# A scan by hand and one the watcher starts must not run side by side: both add the games they find.
_scan_lock = threading.Lock()


def scan_library(library: Library) -> ScanResult:
    with _scan_lock:
        return _scan_library(library)


def _scan_library(library: Library) -> ScanResult:
    root = Path(library.root_path)
    on_disk = {p.name for p in root.iterdir() if has_files(p)} if root.is_dir() else set()

    existing = {g.fs_name: g for g in db_game_handler.get_games_for_library(library.id)}

    new_games = []
    for fs_name in sorted(on_disk - existing.keys()):
        game = db_game_handler.add_game(
            Game(library_id=library.id, fs_name=fs_name, name=fs_name, addons_only=only_addons(root / fs_name))
        )
        new_games.append((game.id, fs_name))

    # Vanished games are only flagged, never deleted, so their metadata and
    # install history survive a temporarily unmounted share.
    missing = 0
    for fs_name, game in existing.items():
        is_missing = fs_name not in on_disk
        changes = {}
        if is_missing != game.missing_from_fs:
            changes["missing_from_fs"] = is_missing
        addons = not is_missing and only_addons(root / fs_name)
        if addons != bool(game.addons_only):
            changes["addons_only"] = addons
        if changes:
            db_game_handler.update_game(game.id, changes)
        missing += is_missing

    return ScanResult(added=len(new_games), missing=missing, total=len(on_disk), new_games=tuple(new_games))
