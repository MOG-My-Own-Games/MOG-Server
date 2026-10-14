"""Mods, patches, DLC and extras kept on the server: the files an install added on top of a game, as one zip per entity.

An entity lives in a folder of its own under ADDONS_PATH (`<ADDONS_PATH>/<game_id>/<category>/<entity_id>/content.zip`),
never in the install cache, so the cache's expiry and its clearing leave it alone. The zip's member names are the
paths the files have under the game's install (relative to the game's root), so a client can extract it over the game.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from config import ADDONS_PATH
from handler.database import db_game_addons_handler
from logger.logger import log
from models.game_addon import ADDON_CATEGORIES, ADDON_NAME_MAX_LENGTH, GameAddon

CONTENT_FILE = "content.zip"
# Where zips are written before the entity exists, so a failed install never leaves one in the library.
_INCOMING = ".incoming"
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9 ._()\[\]+&'-]")


class AddonError(ValueError):
    """Refused: the caller should answer 400 with the message."""


def check_category(category: str) -> str:
    if category not in ADDON_CATEGORIES:
        raise AddonError(f"Category must be one of: {', '.join(ADDON_CATEGORIES)}")
    return category


def sanitize_name(name: str) -> str:
    """A plain file name for the entity: no folders, nothing a shell or a path would take, never empty."""
    base = Path(name.replace("\\", "/")).name.strip()
    cleaned = _UNSAFE_CHARS.sub("_", base)[:ADDON_NAME_MAX_LENGTH].strip(" .")
    if not cleaned:
        raise AddonError("The name is empty")
    return cleaned


def addon_dir(game_id: int, category: str, addon_id: int) -> Path:
    return Path(ADDONS_PATH) / str(game_id) / category / str(addon_id)


def content_path(addon: GameAddon) -> Path:
    return addon_dir(addon.game_id, addon.category, addon.id) / CONTENT_FILE


def _check_zip(source: Path) -> tuple[int, int]:
    """(bytes, files) of a zip whose members all stay inside the game's folder. Refused otherwise."""
    if not zipfile.is_zipfile(source):
        raise AddonError("Not a zip archive")
    with zipfile.ZipFile(source) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        for info in members:
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise AddonError(f"Unsafe path in the archive: {info.filename}")
    return sum(info.file_size for info in members), len(members)


def add_zip(
    game_id: int,
    category: str,
    name: str,
    source: Path,
    user_id: int | None,
    session_id: int | None = None,
) -> GameAddon:
    """Keep a zip as the content of a new entity. `source` is copied, never moved, and never overwrites anything."""
    category = check_category(category)
    name = sanitize_name(name)
    size, count = _check_zip(source)
    row = db_game_addons_handler.add(
        {
            "game_id": game_id,
            "category": category,
            "name": name,
            "size_bytes": size,
            "file_count": count,
            "user_id": user_id,
            "session_id": session_id,
        }
    )
    dest = addon_dir(game_id, category, row.id)
    try:
        dest.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source, dest / CONTENT_FILE)
    except OSError:
        db_game_addons_handler.delete(row.id)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    log.info(f"Addon kept: {category} {name!r} of game {game_id} ({count} files, {size:,} bytes)")
    return row


def list_files(addon: GameAddon, limit: int) -> tuple[int, list[tuple[str, int]]]:
    """(how many files the addon holds, the first `limit` of them as (path, size)), read from its zip."""
    path = content_path(addon)
    if not path.is_file():
        raise AddonError("The addon's content is missing")
    with zipfile.ZipFile(path) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
    return len(members), [(info.filename, info.file_size) for info in members[:limit]]


def build_zip(files: list[tuple[str, Path]]) -> Path:
    """A zip of `files` (member name, file) in the incoming folder, to be kept with add_zip once the install succeeds."""
    incoming = Path(ADDONS_PATH) / _INCOMING
    incoming.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=incoming, suffix=".zip", delete=False)
    handle.close()
    zip_path = Path(handle.name)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for member, source in files:
                zf.write(source, member)
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise
    return zip_path


def discard_zip(zip_path: Path) -> None:
    zip_path.unlink(missing_ok=True)


def remove(addon: GameAddon) -> None:
    """Delete the entity: its row, then its folder."""
    db_game_addons_handler.delete(addon.id)
    shutil.rmtree(addon_dir(addon.game_id, addon.category, addon.id), ignore_errors=True)
