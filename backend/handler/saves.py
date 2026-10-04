"""Storing, pruning and removing save archives on disk, and the rows that describe them."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile

from config import MAX_SAVE_UPLOAD_BYTES, SAVES_BASE_PATH, SAVES_KEEP_VERSIONS
from handler.database import db_device_handler, db_saves_handler
from logger.logger import log
from models.base import utc_now
from models.save_version import MANIFEST_MAX_ENTRIES, SaveVersion
from utils.archive_safety import inspect_archive

_CHUNK = 1024 * 1024
_INCOMING_DIR = ".incoming"


class UploadTooLarge(Exception):
    pass


def saves_root() -> Path:
    return Path(SAVES_BASE_PATH)


def resolve_path(relative: str) -> Path:
    """The absolute file for a stored relative path; never outside the saves directory."""
    root = saves_root().resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"path escapes the saves directory: {relative!r}")
    return path


async def receive_upload(upload: UploadFile) -> Path:
    """Copy an upload to a temporary file inside the saves directory (same filesystem as its
    final place), stopping at MAX_SAVE_UPLOAD_BYTES."""
    incoming = saves_root() / _INCOMING_DIR
    incoming.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=incoming, suffix=".zip")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > MAX_SAVE_UPLOAD_BYTES:
                    raise UploadTooLarge(MAX_SAVE_UPLOAD_BYTES)
                out.write(chunk)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _remove_file(relative: str) -> None:
    try:
        resolve_path(relative).unlink(missing_ok=True)
    except (OSError, ValueError) as e:
        log.warning(f"Could not remove save file {relative}: {e}")


def store_version(
    user_id: int, game_id: int, device_id: int, trigger: str, incoming: Path
) -> tuple[SaveVersion, bool]:
    """Validate and file an uploaded archive; returns (version, created).

    An archive with the same content as the device's newest version is not stored again: the
    existing row comes back with created=False. Afterwards only the newest SAVES_KEEP_VERSIONS
    stay. Raises ArchiveError (and discards the upload) when the archive is not acceptable."""
    try:
        info = inspect_archive(incoming)
    except BaseException:
        incoming.unlink(missing_ok=True)
        raise
    latest = db_saves_handler.get_latest(user_id, game_id, device_id)
    if latest is not None and latest.content_hash == info.content_hash:
        incoming.unlink(missing_ok=True)
        return latest, False

    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    relative = f"{user_id}/{game_id}/{device_id}/{stamp}.zip"
    final = resolve_path(relative)
    final.parent.mkdir(parents=True, exist_ok=True)
    size = incoming.stat().st_size
    shutil.move(incoming, final)
    version = db_saves_handler.add_version(
        SaveVersion(
            user_id=user_id,
            game_id=game_id,
            device_id=device_id,
            trigger=trigger,
            file_path=relative,
            size_bytes=size,
            content_hash=info.content_hash,
            file_count=info.file_count,
            manifest=[{"path": p, "size": s} for p, s in info.files[:MANIFEST_MAX_ENTRIES]],
        )
    )
    for stale in db_saves_handler.delete_beyond_newest(user_id, game_id, device_id, SAVES_KEEP_VERSIONS):
        _remove_file(stale.file_path)
    return version, True


def purge_game(game_id: int) -> None:
    """Delete every user's saves of a game, files included."""
    for user_id in db_saves_handler.delete_for_game(game_id):
        _remove_tree(f"{user_id}/{game_id}")


def purge_user(user_id: int) -> None:
    """Delete a user's saves and devices, files included."""
    db_saves_handler.delete_for_user(user_id)
    db_device_handler.delete_for_user(user_id)
    _remove_tree(str(user_id))


def _remove_tree(relative: str) -> None:
    try:
        shutil.rmtree(resolve_path(relative), ignore_errors=True)
    except ValueError as e:
        log.warning(f"Could not remove saves {relative}: {e}")


def remove_version(version: SaveVersion) -> None:
    db_saves_handler.delete_version(version.id)
    _remove_file(version.file_path)
