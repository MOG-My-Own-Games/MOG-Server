"""The icons in a game's own folder, offered next to the providers' icons when a game is scraped: `.ico` files, and the icon
inside each `.exe` (its first icon group).

An `.exe` is only read as far as its resource table (a bounded walk of it, nothing is run), the icon is made into an `.ico` in
memory and Pillow draws the largest picture of it as a PNG. Those PNGs are kept under `<resources>/media/<game>/found/`, named by
their content, with an index so a folder is not read again while its files are as they were. Choosing one copies it as the game's
own upload (see `read`), so the folder is not needed afterwards."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import struct
from pathlib import Path

from PIL import Image

from handler import media_uploads
from handler.media import LOCAL
from handler.filesystem.installer_detection import PREREQUISITE_DIR_NAMES, is_prerequisite_name
from logger.logger import log
from models.game import Game

MAX_DEPTH = 4  # folders below the game's own
MAX_VISITED = 20_000  # entries looked at in one folder: a huge install tree is not walked to its end
MAX_FILES = 40  # `.exe` and `.ico` files read
MAX_ICO_BYTES = 5 * 1024 * 1024
MAX_ICON_BYTES = 1024 * 1024  # one picture of an icon inside an exe
MAX_ENTRIES = 1000  # in one directory of the resource table
MIN_SIDE, MAX_SIDE = 16, 256
_UNWANTED = re.compile(
    r"^(unins\d*|uninstall|uninst|crashhandler|unitycrashhandler\w*|crashpad\w*|crashreport\w*|python\d*w?)\.", re.IGNORECASE
)  # python.exe: the interpreter a Ren'Py game ships
# Folders that hold a system's files, not the game's: a Wine prefix kept next to the game has its own notepad.exe.
_SYSTEM_DIRS = frozenset(("pfx", "drive_c", "windows", "system32", "syswow64", "dosdevices"))
_URL = re.compile(r"/resources/media/(?P<game>\d+)/found/(?P<name>[0-9a-f]{12}\.png)")

RT_ICON, RT_GROUP_ICON = 3, 14


def _u(data: bytes, fmt: str, offset: int) -> tuple:
    return struct.unpack_from(fmt, data, offset)


class _Pe:
    """Just enough of a Portable Executable to read its resource table."""

    def __init__(self, f) -> None:
        self.f = f
        head = self._read(0, 64)
        if head[:2] != b"MZ":
            raise ValueError("not an executable")
        pe = _u(head, "<I", 60)[0]
        coff = self._read(pe, 24)
        if coff[:4] != b"PE\0\0":
            raise ValueError("not a PE file")
        sections, optional_size = _u(coff, "<H", 6)[0], _u(coff, "<H", 20)[0]
        optional = self._read(pe + 24, optional_size)
        directories = {0x10B: 96, 0x20B: 112}.get(_u(optional, "<H", 0)[0])
        if directories is None or optional_size < directories + 24:
            raise ValueError("no resource table")
        self.rva, _size = _u(optional, "<II", directories + 16)
        table = self._read(pe + 24 + optional_size, 40 * min(sections, 96))
        self.sections = [_u(table, "<8sIIII", 40 * i)[1:] for i in range(len(table) // 40)]  # virtual size, address, raw size, raw pointer
        self.base = self.offset(self.rva) if self.rva else None
        if self.base is None:
            raise ValueError("no resource table")

    def _read(self, offset: int, size: int) -> bytes:
        self.f.seek(offset)
        data = self.f.read(size)
        if len(data) != size:
            raise ValueError("cut short")
        return data

    def offset(self, rva: int) -> int | None:
        for virtual, address, raw, pointer in self.sections:
            if address <= rva < address + max(virtual, raw):
                return rva - address + pointer
        return None

    def directory(self, offset: int) -> list[tuple[int | None, int, bool]]:
        """The entries of the resource directory at `offset` (from the table's start): (id, where it leads, is a directory). A
        named entry has no id."""
        head = self._read(self.base + offset, 16)
        count = min(_u(head, "<H", 12)[0] + _u(head, "<H", 14)[0], MAX_ENTRIES)
        raw = self._read(self.base + offset + 16, 8 * count)
        out = []
        for i in range(count):
            name, target = _u(raw, "<II", 8 * i)
            out.append((None if name & 0x80000000 else name, target & 0x7FFFFFFF, bool(target & 0x80000000)))
        return out

    def leaf(self, offset: int, limit: int) -> bytes | None:
        """The data of the first resource below the directory at `offset` (a type, a name, then a language)."""
        for _depth in range(3):
            entries = self.directory(offset)
            if not entries:
                return None
            _id, target, is_dir = entries[0]
            if not is_dir:
                rva, size = _u(self._read(self.base + target, 16), "<II", 0)
                start = self.offset(rva)
                return self._read(start, size) if start is not None and 0 < size <= limit else None
            offset = target
        return None

    def of_type(self, kind: int) -> list[tuple[int | None, int]]:
        for ident, target, is_dir in self.directory(0):
            if ident == kind and is_dir:
                return [(i, t) for i, t, _ in self.directory(target)]
        return []


def exe_icon(path: Path) -> bytes | None:
    """The first icon group of the executable as the bytes of an `.ico` file, or None (no icon, or not a readable PE file)."""
    try:
        with open(path, "rb") as f:
            pe = _Pe(f)
            groups = pe.of_type(RT_GROUP_ICON)
            if not groups:
                return None
            group = pe.leaf(groups[0][1], MAX_ICON_BYTES)
            if group is None or len(group) < 6:
                return None
            count = min(_u(group, "<H", 4)[0], 64)
            icons = dict(pe.of_type(RT_ICON))
            frames = []
            for i in range(count):
                width, height, colors, _r, planes, bits, _size, number = _u(group, "<BBBBHHIH", 6 + 14 * i)
                if number in icons and (data := pe.leaf(icons[number], MAX_ICON_BYTES)):
                    frames.append((width, height, colors, planes, bits, data))
    except (OSError, ValueError, struct.error):
        return None
    if not frames:
        return None
    out = io.BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(frames)))
    offset = 6 + 16 * len(frames)
    for width, height, colors, planes, bits, data in frames:
        out.write(struct.pack("<BBBBHHII", width, height, colors, 0, planes, bits, len(data), offset))
        offset += len(data)
    for *_rest, data in frames:
        out.write(data)
    return out.getvalue()


def to_png(data: bytes) -> tuple[bytes, int, int] | None:
    """The largest picture of an `.ico` as a PNG of at most 256 pixels (and the size it has), or None when it is unusable
    or too small to be an icon."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            best = image.ico.getimage(max(image.ico.sizes(), key=lambda s: s[0] * s[1])) if hasattr(image, "ico") else image
            best = best.convert("RGBA")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return None
    if min(best.size) < MIN_SIDE:
        return None
    if max(best.size) > MAX_SIDE:
        best.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    best.save(out, "PNG")
    return out.getvalue(), best.width, best.height


def files_of(folder: Path) -> list[Path]:
    """The `.ico` and `.exe` files that may hold the game's icon, the shallowest first: not the uninstaller, a crash reporter or a
    bundled prerequisite (DirectX, the VC++ runtime...), and nothing from a folder of prerequisites."""
    found: list[tuple[int, str, Path]] = []
    visited = 0
    for here, dirs, names in os.walk(folder):
        depth = len(Path(here).relative_to(folder).parts)
        dirs[:] = [d for d in dirs if depth < MAX_DEPTH and d.lower() not in PREREQUISITE_DIR_NAMES | _SYSTEM_DIRS and not d.startswith(".")]
        visited += len(names) + len(dirs)
        for name in names:
            if name.lower().endswith((".ico", ".exe")) and not _UNWANTED.match(name) and not is_prerequisite_name(name):
                found.append((depth, name.lower(), Path(here) / name))
        if visited > MAX_VISITED:
            break
    return [p for _d, _n, p in sorted(found)][:MAX_FILES]


def _picture(path: Path) -> tuple[bytes, int, int] | None:
    try:
        if path.suffix.lower() == ".ico":
            if path.stat().st_size > MAX_ICO_BYTES:
                return None
            return to_png(path.read_bytes())
    except OSError:
        return None
    data = exe_icon(path)
    return to_png(data) if data else None


def found_dir(game_id: int) -> Path:
    return media_uploads.media_dir() / str(game_id) / "found"


def candidates(game: Game) -> list[dict]:
    """The icons of the game's folder as candidates (`source` "local", `label` the file they come from), each once. A game that is
    a single file, or whose folder is gone, has none."""
    if game.library is None:
        return []
    folder = Path(game.library.root_path) / game.fs_name
    if not folder.is_dir():
        return []
    where = found_dir(game.id)
    index_path = where / "index.json"
    try:
        index = json.loads(index_path.read_text())
    except (OSError, ValueError):
        index = {}
    seen, out, changed = set(), [], False
    for path in files_of(folder):
        try:
            stat = path.stat()
        except OSError:
            continue
        key = f"{path.relative_to(folder).as_posix()}|{stat.st_size}|{stat.st_mtime_ns}"
        entry = index.get(key)
        if entry is None or (entry["name"] and not (where / entry["name"]).is_file()):
            picture = _picture(path)
            entry = {"name": None}
            if picture is not None:
                data, width, height = picture
                entry = {"name": f"{hashlib.sha1(data).hexdigest()[:12]}.png", "width": width, "height": height}
                where.mkdir(parents=True, exist_ok=True)
                (where / entry["name"]).write_bytes(data)
            index[key], changed = entry, True
        if entry["name"] and entry["name"] not in seen:
            seen.add(entry["name"])
            url = f"{media_uploads.URL_PREFIX}{game.id}/found/{entry['name']}"
            out.append(
                {"url": url, "thumb": url, "source": LOCAL, "width": entry["width"], "height": entry["height"],
                 "label": path.relative_to(folder).as_posix()}
            )
    if changed:
        try:
            where.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(index))
            tmp.replace(index_path)
        except OSError as e:
            log.warning(f"Could not keep the list of the icons found in {folder}: {e}")
    return out


def read(url: str | None, game_id: int) -> bytes | None:
    """The bytes of a found icon of this game by its URL, or None for any other URL."""
    match = _URL.fullmatch(url or "")
    if match is None or int(match["game"]) != game_id:
        return None
    try:
        return (found_dir(game_id) / match["name"]).read_bytes()
    except OSError:
        return None
