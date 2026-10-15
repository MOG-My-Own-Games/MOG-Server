"""Checks an uploaded save archive before it is stored. The server never extracts it, but
every client does, so a path that escapes the target folder or an archive that inflates into
gigabytes has to be refused here."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_MEMBERS = 20000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


class ArchiveError(ValueError):
    """The archive is not a zip, or holds something a client must not extract."""


@dataclass
class ArchiveInfo:
    file_count: int = 0
    uncompressed_bytes: int = 0
    content_hash: str = ""
    # (path, size) per file, in archive order.
    files: list[tuple[str, int]] = field(default_factory=list)


def _check_name(name: str) -> str:
    if not name or "\0" in name or "\\" in name:
        raise ArchiveError(f"unsafe path in archive: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or (len(name) > 1 and name[1] == ":") or ".." in path.parts:
        raise ArchiveError(f"unsafe path in archive: {name!r}")
    return str(path)


def check_members(members: list[zipfile.ZipInfo], max_bytes: int = MAX_UNCOMPRESSED_BYTES) -> tuple[int, int]:
    """(declared bytes, files) of the members of a zip, from its listing alone, without reading any content: every name must
    stay inside the folder it is extracted to, no member may be a link, and the count and the declared size are bounded
    (an extraction never writes more than a member declares). Raises ArchiveError otherwise."""
    if len(members) > MAX_MEMBERS:
        raise ArchiveError(f"too many entries ({len(members)})")
    files = [m for m in members if not m.is_dir()]
    for member in members:
        _check_name(member.filename)
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ArchiveError(f"symbolic link in archive: {member.filename!r}")
    total = sum(m.file_size for m in files)
    if total > max_bytes:
        raise ArchiveError("archive expands beyond the allowed size")
    return total, len(files)


def inspect_archive(path: Path) -> ArchiveInfo:
    """Validate the zip at `path` and return what it holds. Raises ArchiveError otherwise."""
    if not zipfile.is_zipfile(path):
        raise ArchiveError("not a zip archive")
    info = ArchiveInfo()
    lines: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ArchiveError(f"too many entries ({len(members)})")
            for member in members:
                name = _check_name(member.filename)
                if member.is_dir():
                    continue
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ArchiveError(f"symbolic link in archive: {name!r}")
                digest = hashlib.sha256()
                size = 0
                with archive.open(member) as handle:
                    while chunk := handle.read(_READ_CHUNK):
                        size += len(chunk)
                        info.uncompressed_bytes += len(chunk)
                        if info.uncompressed_bytes > MAX_UNCOMPRESSED_BYTES:
                            raise ArchiveError("archive expands beyond the allowed size")
                        digest.update(chunk)
                info.files.append((name, size))
                lines.append(f"{name}:{digest.hexdigest()}")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError) as e:
        raise ArchiveError(f"unreadable archive: {e}") from e
    info.file_count = len(info.files)
    if not info.file_count:
        raise ArchiveError("archive holds no files")
    info.content_hash = hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()
    return info
