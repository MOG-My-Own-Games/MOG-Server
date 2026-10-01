"""Library scanning: one Game per top-level entry under a Library's root_path.

No platform detection, no ROM hashing - a library is just a folder of
installers/games, and a scan's only job is to keep the `games` table in
sync with what's actually on disk. Metadata (IGDB/SteamGridDB) is matched
separately, not as part of scanning (see docs/TODO.md for auto-matching).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from handler.database import db_game_handler
from models.game import Game
from models.library import Library


@dataclass(frozen=True, slots=True)
class ScanResult:
    added: int
    removed: int
    total: int


def scan_library(library: Library) -> ScanResult:
    root = Path(library.root_path)
    on_disk = {p.name for p in root.iterdir()} if root.is_dir() else set()

    existing = {g.fs_name: g for g in db_game_handler.get_games_for_library(library.id)}

    added = 0
    for fs_name in sorted(on_disk - existing.keys()):
        db_game_handler.add_game(Game(library_id=library.id, fs_name=fs_name, name=fs_name))
        added += 1

    removed = 0
    for fs_name, game in existing.items():
        if fs_name not in on_disk:
            db_game_handler.delete_game(game.id)
            removed += 1

    return ScanResult(added=added, removed=removed, total=len(on_disk))
