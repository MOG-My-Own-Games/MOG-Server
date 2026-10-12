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
import tempfile
from collections.abc import Sequence
from pathlib import Path

from config import INSTALL_TIMEOUT
from logger.logger import log

# `7z l -slt` output: one "Path = ..." / "Size = ..." pair per listed member,
# blank-line separated, "Attributes = D..." marks a directory entry.
_ENTRY_RE = re.compile(r"^(Path|Size|Attributes) = (.*)$")

# A CD ripped sector by sector (CloneCD's .img, a .bin next to a .cue) keeps each sector's 12 bytes of sync, its header and
# its error codes around the 2048 bytes of data: 7z cannot read the file system through them, but can once they are gone.
RAW_IMAGE_EXTENSIONS = frozenset((".img", ".bin"))
RAW_SECTOR_SIZE = 2352
DATA_SECTOR_SIZE = 2048
_SECTOR_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
_RAW_CHUNK_SECTORS = 2048


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
        listed = _list_raw_image(file_path)
        if listed is not None:
            return listed
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


def _list_raw_image(file_path: Path) -> list[tuple[str, int]] | None:
    """What a raw CD image holds, read through a plain copy of its data. None when it is not one (or the copy failed).
    Without this a disc 7z cannot open lists as empty, and looks like a game that has no installer."""
    offset = raw_sector_data_offset(file_path)
    if offset is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="mog-raw-iso-") as tmp:
            iso = Path(tmp) / f"{file_path.stem}.iso"
            if not raw_sectors_to_iso(file_path, iso, offset):
                return None
            return try_list_archive_members(iso)
    except OSError as e:
        log.error(f"Could not convert {file_path}: {e}")
        return None


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


def raw_sector_data_offset(file_path: Path) -> int | None:
    """Where the 2048 data bytes start in each sector of a raw CD image (16 after a mode 1 header, 24 after a mode 2
    one, which has 8 bytes of subheader), or None when the file is not one."""
    if file_path.suffix.lower() not in RAW_IMAGE_EXTENSIONS:
        return None
    try:
        with open(file_path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    if len(head) < 16 or head[:12] != _SECTOR_SYNC:
        return None
    return {1: 16, 2: 24}.get(head[15])


def raw_sectors_to_iso(file_path: Path, iso_path: Path, offset: int) -> bool:
    """Copy the data of every sector of a raw CD image into `iso_path`, up to the first sector without sync (where the data
    track ends and audio, if any, begins). True when anything was written."""
    written = 0
    try:
        with open(file_path, "rb") as src, open(iso_path, "wb") as dst:
            while True:
                chunk = src.read(RAW_SECTOR_SIZE * _RAW_CHUNK_SECTORS)
                if not chunk:
                    break
                out = bytearray()
                for start in range(0, len(chunk) - RAW_SECTOR_SIZE + 1, RAW_SECTOR_SIZE):
                    if chunk[start : start + 12] != _SECTOR_SYNC:
                        dst.write(out)
                        return written + len(out) > 0
                    out += chunk[start + offset : start + offset + DATA_SECTOR_SIZE]
                dst.write(out)
                written += len(out)
    except OSError as e:
        log.error(f"Could not convert the raw disc image {file_path}: {e}")
        return False
    return written > 0


def _extract_raw_image(file_path: Path, dest_dir: Path, exclude: Sequence[str]) -> str | None:
    """Unpack a raw CD image 7z could not read, through a copy of it without the sector framing. None when the file is
    not one or the copy could not be made (the caller then keeps what 7z said)."""
    offset = raw_sector_data_offset(file_path)
    if offset is None:
        return None
    log.info(f"{file_path.name} is a raw CD image: converting it to a plain ISO to read it")
    try:
        with tempfile.TemporaryDirectory(dir=dest_dir.parent, prefix="mog-raw-iso-") as tmp:
            iso = Path(tmp) / f"{file_path.stem}.iso"
            if not raw_sectors_to_iso(file_path, iso, offset):
                return None
            return extract_archive(iso, dest_dir, exclude) or ""
    except OSError as e:
        log.error(f"Could not convert {file_path}: {e}")
        return None


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
        converted = _extract_raw_image(file_path, dest_dir, exclude)
        if converted is not None:
            return converted or None
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
