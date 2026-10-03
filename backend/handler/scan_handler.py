"""Library scanning: one Game per top-level entry under a Library's root_path.

No platform detection, no ROM hashing - a library is just a folder of
installers/games, and a scan's only job is to keep the `games` table in
sync with what's actually on disk. Metadata (IGDB/SteamGridDB) is matched
separately, not as part of scanning (see docs/TODO.md for auto-matching).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from handler.database import db_game_handler
from models.game import Game
from models.library import Library


@dataclass(frozen=True, slots=True)
class ScanResult:
    added: int
    missing: int
    total: int


def has_files(path: Path) -> bool:
    """A file, or a directory with a file anywhere inside: an empty folder is not a game."""
    if not path.is_dir():
        return True
    return any(files for _, _, files in os.walk(path))


def scan_library(library: Library) -> ScanResult:
    root = Path(library.root_path)
    on_disk = {p.name for p in root.iterdir() if has_files(p)} if root.is_dir() else set()

    existing = {g.fs_name: g for g in db_game_handler.get_games_for_library(library.id)}

    added = 0
    for fs_name in sorted(on_disk - existing.keys()):
        db_game_handler.add_game(Game(library_id=library.id, fs_name=fs_name, name=fs_name))
        added += 1

    # Vanished games are only flagged, never deleted, so their metadata and
    # install history survive a temporarily unmounted share.
    missing = 0
    for fs_name, game in existing.items():
        is_missing = fs_name not in on_disk
        if is_missing != game.missing_from_fs:
            db_game_handler.update_game(game.id, {"missing_from_fs": is_missing})
        missing += is_missing

    return ScanResult(added=added, missing=missing, total=len(on_disk))
