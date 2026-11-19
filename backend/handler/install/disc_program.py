"""A game that runs from its disc: what it reads stays on the disc, and its installer copies only the rest.

Barbie Ocean Discovery's setup writes its data and a shortcut to `abod.exe`, which it never copies: the game starts from the
disc and reads its ASSETS folder there. The install then has nothing to launch, and the client finds no executable. So what the
disc holds for the game and the install lacks is added to it. What is left out is the setup's own machinery: the cabinets,
the setup programs and the redistributables.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from handler.install.executables import REDISTRIBUTABLE_FOLDERS, has_game_program

# At the top of the disc only: a data folder may hold a file with such a name.
_SETUP_NAME_PREFIXES = ("setup", "install", "autorun", "unins", "uninst", "_")  # "_" is InstallShield's: _setup.dll, _isdel.exe
_SETUP_SUFFIXES = (".cab",)


def _is_setup_file(relative: Path) -> bool:
    return len(relative.parts) == 1 and (
        relative.name.lower().startswith(_SETUP_NAME_PREFIXES) or relative.suffix.lower() in _SETUP_SUFFIXES
    )


def _disc_game_files(disc_root: Path) -> list[Path]:
    """The files of the disc that are not the setup's, relative to it."""
    kept: list[Path] = []
    for folder, dirs, names in os.walk(disc_root):  # links are not followed: the sandbox links a Games folder into the disc
        dirs[:] = [d for d in dirs if not d.lower().startswith(REDISTRIBUTABLE_FOLDERS)]
        for name in names:
            path = Path(folder, name)
            relative = path.relative_to(disc_root)
            if not path.is_symlink() and not _is_setup_file(relative):
                kept.append(relative)
    return kept


def _signature(path: Path) -> tuple[str, int] | None:
    """What tells a disc file from the same file the installer wrote, wherever it put it."""
    try:
        return path.name.lower(), path.stat().st_size
    except OSError:
        return None


def missing_disc_files(disc_root: Path, installed: list[Path]) -> list[Path]:
    """The game's files of the disc that the install lacks, relative to the disc: none when the install has no program and
    none of them is one, since data alone would not make the install launchable."""
    present = {s for s in map(_signature, installed) if s is not None}
    missing = [r for r in _disc_game_files(disc_root) if _signature(disc_root / r) not in present]
    if not has_game_program(installed) and not has_game_program([disc_root / r for r in missing]):
        return []
    return missing


def copy_missing_disc_files(disc_root: Path, dest_root: Path, installed: list[Path]) -> list[Path]:
    """Add them to the install under `dest_root`, never over a file of it. Returns the files added."""
    added: list[Path] = []
    for relative in missing_disc_files(disc_root, installed):
        dest = dest_root / relative
        if dest.exists() or dest.is_symlink():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(disc_root / relative, dest)
        except OSError:  # another filesystem
            shutil.copy2(disc_root / relative, dest)
        added.append(dest)
    return added
