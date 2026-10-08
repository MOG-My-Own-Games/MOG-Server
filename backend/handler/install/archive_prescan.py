# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Extract-then-rescan for installer candidates that are themselves an
archive or disc image (a distributor .zip, a game ISO, ...) rather than a
directly-runnable file.

`installer_detection.detect_installer_candidates` only classifies files by
name/extension - it never looks inside an archive. This module bridges that
gap: extract the archive's full contents into a scratch directory, then run
the exact same detection logic against what's actually inside it, reusing
detection rather than inventing a second ranking scheme.

The scratch directory is a plain `tempfile.TemporaryDirectory()` (host /tmp
by default) - deliberately never under `INSTALL_CACHE_PATH`, since it holds
a copy of the game's own archive contents, not the install's own output. The
caller owns its lifetime and must `.cleanup()` it once the installer has
actually run (the sandbox needs to keep reading from it for the whole run).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from handler.filesystem.installer_detection import (
    ARCHIVE_EXTENSIONS,
    DISC_IMAGE_EXTENSIONS,
    RANK_ARCHIVE,
    RANK_DISC_IMAGE,
    RANK_NESTED_EXECUTABLE,
    DetectedFile,
    InstallerCandidate,
    detect_installer_candidates,
    looks_portable,
)
from logger.logger import log
from models.install_session import InstallPhase
from utils.archives import extract_archive_member, extract_archive_tree, list_archive_members, try_list_archive_members

_NESTED_RANKS = (RANK_DISC_IMAGE, RANK_ARCHIVE)
# Archives inside archives are unpacked at most this many levels deep.
MAX_NESTING = 3
# An archive inside the archive is looked into before anything is unpacked, by taking just that file out; a bigger
# one than this is not worth it, and is unpacked and looked at as before.
PEEK_MAX_BYTES = 2 * 1024**3

_PRE_SCAN_EXTENSIONS = ARCHIVE_EXTENSIONS | DISC_IMAGE_EXTENSIONS


def is_archive_candidate(path: Path) -> bool:
    """Whether `path` needs extraction before it can be searched for an
    installer, rather than being runnable/openable as-is."""
    return path.suffix.lower() in _PRE_SCAN_EXTENSIONS


def source_phase(path: Path) -> InstallPhase:
    """The status to report while `path` is being unpacked."""
    if path.suffix.lower() in DISC_IMAGE_EXTENSIONS:
        return InstallPhase.MOUNTING
    return InstallPhase.EXTRACTING


def list_source_candidates(source: Path) -> list[InstallerCandidate]:
    """Runnable installers inside an archive/disc image, from its member
    listing alone (nothing is extracted)."""
    files = [DetectedFile(path=name, size_bytes=size) for name, size in list_archive_members(source)]
    candidates = detect_installer_candidates(files)
    executables = [c for c in candidates if c.rank <= RANK_NESTED_EXECUTABLE]
    # A collection of archives (one zip per game) has nothing runnable at
    # the top level; offer the nested archives, unpacked one level further
    # at install time.
    return executables or [c for c in candidates if c.rank in _NESTED_RANKS]


def extract_suggested(candidates: list[InstallerCandidate]) -> bool:
    """Whether what a listing of an archive found says the game inside needs no installer: nothing that looks
    like one (a known installer name, or a name with setup or install in it) and no archive or disc image to
    unpack further. Executables with any other name, at the top level or buried in folders, are the game itself
    and its tools; so is an archive with nothing runnable at all. The same rule as for a game's own folder."""
    return looks_portable(candidates)


def _list_files_flat(root: Path) -> list[DetectedFile]:
    detected: list[DetectedFile] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        detected.append(DetectedFile(path=p.relative_to(root).as_posix(), size_bytes=size))
    return detected


def _detect_listing(members: list[tuple[str, int]]) -> list[InstallerCandidate]:
    return detect_installer_candidates([DetectedFile(path=name, size_bytes=size) for name, size in members])


def _peek_inside(archive_path: Path, member: str, extract_root: Path) -> list[InstallerCandidate] | None:
    """What a nested archive holds, read before the archive around it is unpacked: just that file is taken out, into
    its place in `extract_root`, and listed. None when it could not be looked into (it is then found out the long
    way, by unpacking)."""
    if not extract_archive_member(archive_path, member, extract_root):
        return None
    inside = try_list_archive_members(extract_root / member)
    return None if inside is None else _detect_listing(inside)


def extract_and_rescan(
    archive_path: Path,
    installer_path: str | None = None,
) -> tuple[tempfile.TemporaryDirectory[str], Path, InstallerCandidate] | None:
    """Extract `archive_path` and rank installer candidates inside it.

    Before anything is unpacked its member listing is read: an archive with no installer in it, or without the
    one asked for, is turned down at once instead of after unpacking all of it. The same is done for the archive
    inside it that would be unpacked next, looking at just that file. An archive that cannot be listed is
    unpacked and searched as before.

    Returns `(temp_dir, extract_root, chosen)` on success - the caller owns
    `temp_dir` and must clean it up once done with it; the installer to run
    is `extract_root / chosen.path`. `chosen` is `installer_path` when given
    (and present in the extracted tree), else the top-ranked candidate.
    Returns None (with the temp dir already cleaned up) if extraction failed
    or nothing installer-like was found inside.
    """
    temp_dir = tempfile.TemporaryDirectory(prefix="mog-install-extract-")
    extract_root = Path(temp_dir.name)

    def give_up(why: str) -> None:
        log.error(why)
        temp_dir.cleanup()

    skip: list[str] = []
    listed = try_list_archive_members(archive_path)
    if listed is not None:
        before = _detect_listing(listed)
        if not before:
            give_up(f"No installer found inside archive {archive_path} (listed, not extracted)")
            return None
        first = next((c for c in before if c.path == installer_path), None) if installer_path else before[0]
        if first is None:
            give_up(f"Installer {installer_path} not found inside {archive_path} (listed, not extracted)")
            return None
        if first.rank in _NESTED_RANKS and first.file_size_bytes <= PEEK_MAX_BYTES:
            inner = _peek_inside(archive_path, first.path, extract_root)
            if inner is not None and not inner:
                give_up(f"No installer found inside {first.path} in {archive_path} (looked into it, not unpacked)")
                return None
            if inner is not None:
                skip.append(first.path)  # already out: the rest is unpacked around it

    # Nothing left to unpack when the file taken out was all there was.
    rest = [name for name, _size in (listed or []) if name not in skip]
    if not (skip and not rest) and not extract_archive_tree(archive_path, extract_root, skip):
        give_up(f"Failed to extract archive contents from {archive_path}")
        return None

    candidates = detect_installer_candidates(_list_files_flat(extract_root))
    if not candidates:
        give_up(f"No installer found inside extracted archive {archive_path}")
        return None

    if installer_path:
        picked = next((c for c in candidates if c.path == installer_path), None)
        if picked is None:
            give_up(f"Installer {installer_path} not found inside {archive_path}")
            return None
    else:
        picked = candidates[0]

    for _ in range(MAX_NESTING):
        if picked.rank not in _NESTED_RANKS:
            break
        nested = _unpack_nested(extract_root, picked)
        if nested is None:
            temp_dir.cleanup()
            return None
        picked = nested
    else:
        if picked.rank in _NESTED_RANKS:
            give_up(f"Archive {archive_path} is nested too deeply")
            return None

    return temp_dir, extract_root, picked


def _unpack_nested(extract_root: Path, archive: InstallerCandidate) -> InstallerCandidate | None:
    """Unpack an archive/disc image found inside the extracted tree next to
    itself and return its top candidate, with a path relative to
    `extract_root`."""
    dest_rel = f"{archive.path}.extracted"
    dest = extract_root / dest_rel
    dest.mkdir(parents=True, exist_ok=True)
    if not extract_archive_tree(extract_root / archive.path, dest):
        log.error(f"Failed to extract nested archive {archive.path}")
        return None
    inner = detect_installer_candidates(_list_files_flat(dest))
    if not inner:
        log.error(f"No installer found inside nested archive {archive.path}")
        return None
    top = inner[0]
    return InstallerCandidate(
        path=f"{dest_rel}/{top.path}",
        file_name=top.file_name,
        file_size_bytes=top.file_size_bytes,
        rank=top.rank,
        kind=top.kind,
    )
