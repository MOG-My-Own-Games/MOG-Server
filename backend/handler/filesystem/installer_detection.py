# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Pure installer-candidate detection.

Given a flat list of files found under a game's directory (relative paths + sizes),
rank them so a client can pick the most likely installer. Kept free of any
filesystem or DB access so it is trivially unit-testable.

Detection order (lower rank = higher priority):
  0. Known GOG/setup installer names: gog-*.exe, setup.exe, install.exe, setup*.exe
     (also .msi variants).
  1. Any executable installer in the top-level game folder (.exe/.msi/.bat).
  2. Any executable installer anywhere (recursive) in the game folder.
  3. Disc images (.iso/.cue/.chd/.ccd/.bin/.img/.mds/.mdf/.nrg).
  4. Archives (.zip/.7z/.rar/.tar/.gz/.tgz/.tbz2/.txz/.bz2/.xz).
  5. Generic installers packaged as a shell script or AppImage (.sh/.run/.appimage); a small .sh is the game's
     own start script and is not one.
When nothing matches, the caller falls back to a manual file picker.

Like RomM's file categories, a top-level folder inside the game directory
named after a category (dlc/dlcs, mod/mods, update, patch, ...) tags every file
under it with that category. Those candidates rank after the base game's and
are never the default pick, so a game's DLC or mods can be installed on top of
it. Folders holding no installable content (manual, soundtrack, ...) are skipped.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

# Rank buckets.
RANK_KNOWN_INSTALLER = 0
RANK_TOP_LEVEL_EXECUTABLE = 1
RANK_NESTED_EXECUTABLE = 2
RANK_DISC_IMAGE = 3
RANK_ARCHIVE = 4
RANK_LINUX_INSTALLER = 5

# Case-insensitive glob patterns for well-known Windows installer entry points.
KNOWN_INSTALLER_PATTERNS: tuple[str, ...] = (
    "gog-*.exe",
    "setup.exe",
    "install.exe",
    "setup*.exe",
    "gog-*.msi",
    "setup.msi",
    "install.msi",
    "setup*.msi",
)

# `InstallerCandidate.kind` values that need unpacking before anything runs.
ARCHIVE_SOURCE_KINDS: frozenset[str] = frozenset(("disc image", "archive"))

EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset((".exe", ".msi", ".bat"))
DISC_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    (".iso", ".cue", ".chd", ".ccd", ".bin", ".img", ".mds", ".mdf", ".nrg")
)
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".tbz2", ".txz", ".bz2", ".xz")
)
LINUX_INSTALLER_EXTENSIONS: frozenset[str] = frozenset((".sh", ".run", ".appimage"))

# A shell script smaller than this is the game's own start script (a Ren'Py game's `Game.sh`, a `start.sh`), not an
# installer: an installer script carries the game (GOG's and itch's are many megabytes). A size of 0 is "not known".
MIN_INSTALLER_SCRIPT_BYTES = 1024 * 1024

# Bundled prerequisite installers told by their own name, wherever they sit (a Ren'Py game keeps dxwebsetup.exe in
# lib/windows-i686, which is no folder named for it): DirectX, the VC++ and .NET runtimes, PhysX, OpenAL, XNA.
_PREREQUISITE_NAME = re.compile(
    r"^(dx[\w. -]*setup|dxweb|directx|vc_?redist|vcruntime|dotnet|ndp\d|windowsdesktop-runtime|oalinst|physx|xnafx)",
    re.IGNORECASE,
)

# Category folder names (singular; plural "s"/"es" forms also match), as in RomM.
GAME_CATEGORY = "game"
ADDON_CATEGORIES: tuple[str, ...] = (
    "dlc",
    "mod",
    "update",
    "patch",
    "hack",
    "translation",
    "demo",
    "prototype",
)
NON_INSTALLABLE_CATEGORIES: tuple[str, ...] = ("manual", "walkthrough", "soundtrack", "screenshot", "cheat")

_CATEGORY_BY_FOLDER: dict[str, str] = {
    form: category
    for category in (*ADDON_CATEGORIES, *NON_INSTALLABLE_CATEGORIES)
    for form in (category, f"{category}s", f"{category}es")
}

# Bundled prerequisite installers (VC++ Redistributable, DirectX, .NET, PhysX,
# OpenAL, ...) ship inside a conventionally-named subfolder in virtually every
# PC game release and must never be picked as *the* installer: running one
# does nothing for the game itself, and its own throwaway output can get
# captured as if it were the actual install once it exits. Matched by folder
# name rather than by the executable's own name, since the prerequisite's
# binary varies by vendor/version (vcredist_x64.exe, dxsetup.exe,
# dotnetfx35.exe, PhysX_Setup.exe, oalinst.exe, ...) while the convention of
# where they live doesn't.
PREREQUISITE_DIR_NAMES: frozenset[str] = frozenset(
    (
        "_commonredist",
        "commonredist",
        "redist",
        "redistributables",
        "prerequisites",
        "prereqs",
        "directx",
        "dxsetup",
        "vcredist",
        "dotnet",
    )
)


@dataclass(frozen=True, slots=True)
class DetectedFile:
    """A file discovered under a game's directory, with a POSIX relative path."""

    path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class InstallerCandidate:
    path: str
    file_name: str
    file_size_bytes: int
    rank: int
    kind: str
    category: str = GAME_CATEGORY


def category_for_path(posix: PurePosixPath) -> str:
    """The category a file's top-level folder gives it, GAME_CATEGORY outside any."""
    if len(posix.parts) < 2:
        return GAME_CATEGORY
    return _CATEGORY_BY_FOLDER.get(posix.parts[0].lower(), GAME_CATEGORY)


def is_addon_folder(name: str) -> bool:
    """Whether a top-level folder name marks add-on content (mods, DLC, patches, ...)."""
    return _CATEGORY_BY_FOLDER.get(name.lower()) in ADDON_CATEGORIES


def is_mods_folder(name: str) -> bool:
    """Whether a top-level folder name is the game's mods folder ("mods", "mod")."""
    return _CATEGORY_BY_FOLDER.get(name.lower()) == "mod"


def is_base_installer(path: str) -> bool:
    """Whether a game-relative file would be offered as an installer of the base game."""
    candidate = _classify(DetectedFile(path=path, size_bytes=0))
    return candidate is not None and candidate.category == GAME_CATEGORY


def _matches_known_installer(name_lower: str) -> bool:
    return any(fnmatch.fnmatch(name_lower, pat) for pat in KNOWN_INSTALLER_PATTERNS)


def _under_prerequisite_dir(posix: PurePosixPath) -> bool:
    return any(part.lower() in PREREQUISITE_DIR_NAMES for part in posix.parts[:-1])


def _classify(file: DetectedFile) -> InstallerCandidate | None:
    candidate = _classify_file(file)
    if candidate is None:
        return None
    category = category_for_path(PurePosixPath(file.path))
    return replace(candidate, category=category)


def _classify_file(file: DetectedFile) -> InstallerCandidate | None:
    posix = PurePosixPath(file.path)
    name = posix.name
    name_lower = name.lower()
    ext = posix.suffix.lower()
    is_top_level = len(posix.parts) == 1

    if category_for_path(posix) in NON_INSTALLABLE_CATEGORIES:
        return None

    if ext in EXECUTABLE_EXTENSIONS and (_under_prerequisite_dir(posix) or _PREREQUISITE_NAME.match(posix.stem)):
        return None

    if ext == ".sh" and 0 < file.size_bytes < MIN_INSTALLER_SCRIPT_BYTES:
        return None  # the game's own start script, not an installer

    if ext in EXECUTABLE_EXTENSIONS and _matches_known_installer(name_lower):
        return _make(file, RANK_KNOWN_INSTALLER, "known installer")

    if ext in EXECUTABLE_EXTENSIONS:
        if is_top_level:
            return _make(file, RANK_TOP_LEVEL_EXECUTABLE, "executable (top level)")
        return _make(file, RANK_NESTED_EXECUTABLE, "executable (nested)")

    if ext in DISC_IMAGE_EXTENSIONS:
        return _make(file, RANK_DISC_IMAGE, "disc image")

    if ext in ARCHIVE_EXTENSIONS:
        return _make(file, RANK_ARCHIVE, "archive")

    if ext in LINUX_INSTALLER_EXTENSIONS:
        return _make(file, RANK_LINUX_INSTALLER, "linux installer")

    return None


def _make(file: DetectedFile, rank: int, kind: str) -> InstallerCandidate:
    return InstallerCandidate(
        path=file.path,
        file_name=PurePosixPath(file.path).name,
        file_size_bytes=file.size_bytes,
        rank=rank,
        kind=kind,
    )


def detect_installer_candidates(files: list[DetectedFile]) -> list[InstallerCandidate]:
    """Rank installer candidates from a flat file listing.

    Base-game candidates come first; each group is sorted by rank (priority), then
    by descending size (bigger installers first within a bucket), then by path.
    """
    candidates = [c for c in (_classify(f) for f in files) if c is not None]
    candidates.sort(key=lambda c: (c.category != GAME_CATEGORY, c.rank, -c.file_size_bytes, c.path))
    return candidates


_INSTALLER_NAME_HINTS = ("setup", "install")


def is_probable_installer(candidate: InstallerCandidate) -> bool:
    """Whether a candidate is likely an installer for real: a known installer name, anything named like one
    (setup, install; an uninstaller is not), an archive or disc image to unpack, or a Linux installer script.
    A bare executable with some other name is more likely the game itself."""
    if candidate.rank in (RANK_KNOWN_INSTALLER, RANK_DISC_IMAGE, RANK_ARCHIVE, RANK_LINUX_INSTALLER):
        return True
    name = candidate.file_name.lower()
    return any(hint in name for hint in _INSTALLER_NAME_HINTS) and "uninst" not in name


_WINDOWS_TAGS = frozenset(("win", "win32", "win64", "windows", "pc", "x64", "x86", "w32", "w64"))
_OTHER_PLATFORM_TAGS = frozenset(("mac", "macos", "osx", "linux", "android", "apk", "ios"))


def build_platform(file_name: str) -> str | None:
    """"windows" or "other" for an archive named after the system it is a build for (`game-1.2-pc.zip`,
    `game-mac.zip`, `game_linux.tar.bz2`); None when its name says nothing about it."""
    words = set(re.split(r"[^a-z0-9]+", file_name.lower()))
    if words & _WINDOWS_TAGS:
        return "windows"
    if words & _OTHER_PLATFORM_TAGS:
        return "other"
    return None


def is_platform_bundle(candidates: list[InstallerCandidate]) -> bool:
    """Whether the archives a listing found are the same game built for several systems, with one for Windows: a
    release that carries a `-pc`, a `-mac` and a `-linux` build is no installer, it is a game to unpack (see
    `handler.install.archive_prescan.unwrap_platform_builds`)."""
    probable = [c for c in candidates if c.category == GAME_CATEGORY and is_probable_installer(c)]
    builds = [build_platform(c.file_name) for c in probable if c.rank == RANK_ARCHIVE]
    return bool(probable) and len(builds) == len(probable) and "windows" in builds and all(builds)


def looks_portable(candidates: list[InstallerCandidate]) -> bool:
    """Whether a game's folder holds no installer for the game (only executables that are probably the game
    itself, or nothing runnable): then it needs none, and the person is offered its executables and the choice
    to use the files as they are. Add-ons (DLC, mods, patches) do not count. The same game packed once per system
    (a `-pc`, a `-mac`, a `-linux` archive) needs none either."""
    if is_platform_bundle(candidates):
        return True
    return not any(c.category == GAME_CATEGORY and is_probable_installer(c) for c in candidates)


def pick_default_installer(candidates: list[InstallerCandidate]) -> InstallerCandidate | None:
    """The top-ranked candidate, or None when there is nothing to run.

    Lets a client start an install without naming a file (the CLI, ...) and
    get the same choice a human would make first. The winner may be an
    archive or disc image, whose installer is then resolved after unpacking
    it (see handler.install.archive_prescan). DLC/mod candidates are never
    the default.
    """
    if candidates and candidates[0].category == GAME_CATEGORY:
        return candidates[0]
    return None
