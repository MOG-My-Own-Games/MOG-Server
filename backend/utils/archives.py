"""Archive/disc-image listing and extraction, shelled out to `7z` (p7zip-full,
installed in the server image) for every format it understands (zip, 7z, rar,
tar*, iso, cue/bin, ...). A single tool for every format trades the
performance RomM's utils/archives.py gets from format-native Python libraries
(zipfile/tarfile first, 7z as a fallback) for a much smaller surface - worth
revisiting if a specific format turns out to need it (see docs/TODO.md).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from config import INSTALL_TIMEOUT
from logger.logger import log

# `7z l -slt` output: one "Path = ..." / "Size = ..." pair per listed member,
# blank-line separated, "Attributes = D..." marks a directory entry.
_ENTRY_RE = re.compile(r"^(Path|Size|Attributes) = (.*)$")


def list_archive_members(file_path: Path) -> list[tuple[str, int]]:
    """List `(member_path, size)` for every file in an archive or disc image
    without extracting anything. Empty when it holds no files or cannot be read."""
    return try_list_archive_members(file_path) or []


def try_list_archive_members(file_path: Path) -> list[tuple[str, int]] | None:
    """Like `list_archive_members`, but None when the archive could not be read at all, so an unreadable one is
    told apart from one that is empty."""
    try:
        result = subprocess.run(
            ["7z", "l", "-slt", "-ba", str(file_path)],
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.error(f"Error listing archive {file_path}: {e}")
        return None

    members: list[tuple[str, int]] = []
    path: str | None = None
    size = 0
    is_dir = False
    for line in result.stdout.splitlines():
        if not line.strip():
            if path is not None and not is_dir:
                members.append((path, size))
            path, size, is_dir = None, 0, False
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key == "Path":
            path = value
        elif key == "Size":
            size = int(value) if value.isdigit() else 0
        elif key == "Attributes":
            is_dir = value.startswith("D")
    if path is not None and not is_dir:
        members.append((path, size))
    return members


def _why_extraction_failed(file_path: Path, output: str) -> str:
    """What 7z said went wrong, in a line a person can read."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    first = next((line for line in lines if line.startswith("ERROR")), lines[-1] if lines else "no output")
    count = next((line for line in lines if line.startswith("Sub items Errors:")), None)
    text = f"7-Zip: {first}" + (f" ({count.lower()})" if count else "")
    if "Unsupported Method" in output and file_path.suffix.lower() == ".rar":
        text += (
            ". This 7-Zip build cannot unpack RAR archives: the server image needs the official 7-Zip "
            "(see the Dockerfile)"
        )
    return text


def extract_archive(file_path: Path, dest_dir: Path, exclude: Sequence[str] = ()) -> str | None:
    """Extract every member of an archive/disc image into `dest_dir`, preserving its internal directory
    structure, except the members named in `exclude`. Returns None when it worked, else why it did not."""
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    # -spd: the names are names, not patterns (a member may be called "Game [GOG] (1).iso").
    skipped = ["-spd", *(f"-x!{name}" for name in exclude)] if exclude else []
    try:
        subprocess.run(
            ["7z", "x", f"-o{dest_dir}", "-y", *skipped, str(file_path)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=INSTALL_TIMEOUT,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        reason = _why_extraction_failed(file_path, f"{e.stdout or ''}\n{e.stderr or ''}")
        log.error(f"Error extracting archive tree from {file_path}: {reason}")
        return reason
    except subprocess.TimeoutExpired:
        log.error(f"Extracting {file_path} timed out")
        return "extraction timed out"
    except OSError as e:
        log.error(f"Error extracting archive tree from {file_path}: {e}")
        return f"7z could not be run: {e}"
    if not any(p.is_file() for p in dest_dir.rglob("*")):
        return "the archive holds no files"
    return None


def extract_archive_member(file_path: Path, member: str, dest_dir: Path) -> bool:
    """Extract one member of an archive/disc image into `dest_dir`, keeping its path inside the archive. True when
    the file is there afterwards. Used to look inside an archive that is itself in an archive without unpacking
    everything around it."""
    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["7z", "x", f"-o{dest_dir}", "-y", "-spd", str(file_path), member],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=INSTALL_TIMEOUT,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.warning(f"Could not extract {member} from {file_path}: {e}")
        return False
    return (dest_dir / member).is_file()


def extract_archive_tree(file_path: Path, dest_dir: Path, exclude: Sequence[str] = ()) -> bool:
    """Extract every member of an archive/disc image into `dest_dir`,
    preserving its internal directory structure, except those in `exclude`.

    Returns True if extraction succeeded and wrote at least one file.
    """
    return extract_archive(file_path, dest_dir, exclude) is None
