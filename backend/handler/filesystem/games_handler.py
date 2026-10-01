# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Filesystem access scoped to a Game's own directory (or file) under its
Library's root_path. Replaces the ROM-path subset of RomM's
handler/filesystem/roms_handler.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.game import Game
from utils.filesystem import iter_files

from .installer_detection import (
    DetectedFile,
    InstallerCandidate,
    detect_installer_candidates,
)


class FSGamesHandler:
    def get_game_root_abs_path(self, game: Game) -> Path:
        """Absolute path of the game's own directory (or file) in its library."""
        return Path(game.library.root_path, game.fs_name)

    def list_game_files_flat(self, game: Game) -> list[DetectedFile]:
        """List every file under a game as POSIX-relative paths with sizes.

        For a single-file game the only entry is the file itself. For a
        directory game this walks recursively. Paths are relative to the
        game's own directory (or the file name for single files).
        """
        game_root = self.get_game_root_abs_path(game)

        detected: list[DetectedFile] = []
        if game_root.is_dir():
            for f_path, file_name in iter_files(str(game_root), recursive=True):
                abs_file = Path(f_path, file_name)
                try:
                    size = abs_file.stat().st_size
                except OSError:
                    continue
                rel = abs_file.relative_to(game_root).as_posix()
                detected.append(DetectedFile(path=rel, size_bytes=size))
        else:
            try:
                size = game_root.stat().st_size
            except OSError:
                size = 0
            detected.append(DetectedFile(path=game.fs_name, size_bytes=size))
        return detected

    def get_installer_candidates(self, game: Game) -> list[InstallerCandidate]:
        """Detect and rank installer candidates inside a game's directory."""
        return detect_installer_candidates(self.list_game_files_flat(game))

    def resolve_installer_abs_path(self, game: Game, installer_rel_path: str) -> str:
        """Resolve a game-relative installer path to a validated absolute path.

        Guards against path traversal: the resolved path must stay inside the
        game's own directory. A single-file game has no directory of its own
        to root against, so there the only valid path is the game's own file
        name, never an arbitrary sibling.
        """
        game_root = self.get_game_root_abs_path(game).resolve()
        if not game_root.is_dir():
            if installer_rel_path != game.fs_name:
                raise ValueError("Installer path escapes the game's directory")
            if not game_root.is_file():
                raise FileNotFoundError(f"Installer not found: {installer_rel_path}")
            return str(game_root)

        candidate = (game_root / installer_rel_path).resolve()
        if game_root != candidate and game_root not in candidate.parents:
            raise ValueError("Installer path escapes the game's directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"Installer not found: {installer_rel_path}")
        return str(candidate)


fs_game_handler = FSGamesHandler()
