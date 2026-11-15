"""Storing, pruning and removing save archives on disk, and the rows that describe them."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from anyio import CapacityLimiter, to_thread
from fastapi import UploadFile

from config import MAX_SAVE_UPLOAD_BYTES, SAVES_BASE_PATH, SAVES_KEEP_VERSIONS
from handler.database import db_device_handler, db_save_rules_handler, db_saves_handler, db_settings_handler
from logger.logger import log
from models.base import utc_now
from models.game_save_rule import BLACKLIST, WHITELIST, covers, is_left_out
from models.save_version import MANIFEST_MAX_ENTRIES, SaveVersion
from models.settings import SAVES_KEEP_MAX, SAVES_KEEP_MIN
from utils.archive_safety import ArchiveError, inspect_archive, rewrite_without

_CHUNK = 1024 * 1024
_INCOMING_DIR = ".incoming"

T = TypeVar("T")

# Blocking save work runs on threads of its own, counted apart from the shared pool that file downloads and installs
# keep busy, so a save never waits behind them (it only waits for another save).
SAVES_CHANNELS = 2
_SAVES_LIMITER = CapacityLimiter(SAVES_CHANNELS)


async def run_on_saves_channel(fn: Callable[..., T], *args: object) -> T:
    return await to_thread.run_sync(fn, *args, limiter=_SAVES_LIMITER)


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


def keep_versions(settings) -> int:
    """How many versions of a game are kept for each device: the setting, else SAVES_KEEP_VERSIONS (the environment's, 3)."""
    value = settings.saves_keep_versions
    return SAVES_KEEP_VERSIONS if value is None else min(max(value, SAVES_KEEP_MIN), SAVES_KEEP_MAX)


def rules_for(game_ids: list[int]) -> tuple[list[str], list[str]]:
    """(blacklisted, whitelisted) paths of a title, from the rules of all its versions."""
    rows = db_save_rules_handler.get_for_games(game_ids)
    return [r.path for r in rows if r.kind == BLACKLIST], [r.path for r in rows if r.kind == WHITELIST]


def _drop_covered(source: Path, rules: list[str], target: Path, keep: list[str] | None = None) -> tuple[int, int]:
    """Copy an archive without the files the rules name, except the ones `keep` (the whitelist) names."""
    keep = keep or []
    return rewrite_without(
        source, target, lambda name: any(covers(rule, name) for rule in rules) and not any(covers(k, name) for k in keep)
    )


def store_version(
    user_id: int, game_id: int, device_id: int, trigger: str, incoming: Path, title_ids: list[int] | None = None
) -> tuple[SaveVersion, bool]:
    """Validate and file an uploaded archive; returns (version, created).

    An archive with the same content as the device's newest version is not stored again: the
    existing row comes back with created=False. That version is the newest of any version of the title (`title_ids`), so the
    same files are not kept twice because two versions saved them. Afterwards only the newest `keep_versions()` of this game
    stay. Raises ArchiveError (and discards the upload) when the archive is not acceptable."""
    try:
        info = inspect_archive(incoming)
        blacklist, whitelist = rules_for(title_ids or [game_id])
        if any(is_left_out(name, blacklist, whitelist) for name, _size in info.files):
            # An older client, or one that has not read the rules yet: the archive is stored without what is blacklisted.
            cleaned = incoming.with_suffix(".clean.zip")
            try:
                kept, dropped = _drop_covered(incoming, blacklist, cleaned, whitelist)
                if not kept:
                    raise ArchiveError("every file of the archive is blacklisted")
                log.debug(f"Saves: game {game_id} upload had {dropped} blacklisted files, left out")
                cleaned.replace(incoming)
            finally:
                cleaned.unlink(missing_ok=True)
            info = inspect_archive(incoming)
    except BaseException:
        incoming.unlink(missing_ok=True)
        raise
    latest = db_saves_handler.get_latest_of_games(user_id, title_ids or [game_id], device_id)
    if latest is not None and latest.content_hash == info.content_hash:
        log.debug(f"Saves: game {game_id} device {device_id} upload identical to version {latest.id}, not stored")
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
    for stale in db_saves_handler.delete_beyond_newest(
        user_id, game_id, device_id, keep_versions(db_settings_handler.get_settings())
    ):
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


def remove_files(version: SaveVersion, paths: list[str], keep: list[str] | None = None) -> SaveVersion | None:
    """Take the files at `paths` (a file or a folder with all that is in it) out of a version's archive and update what the row
    says it holds. The version goes when nothing is left (None). The archive is rewritten beside the old one and replaces it
    only when it is complete. What `keep` names (paths, as `paths`) stays whatever `paths` says."""
    archive = resolve_path(version.file_path)
    if not archive.is_file():
        raise ArchiveError("The archive is gone from disk")
    target = archive.with_suffix(".edit.zip")
    try:
        kept, dropped = _drop_covered(archive, paths, target, keep)
        if not dropped:
            return version
        if not kept:
            remove_version(version)
            return None
        info = inspect_archive(target)
        size = target.stat().st_size
        target.replace(archive)
    finally:
        target.unlink(missing_ok=True)
    return db_saves_handler.update_contents(
        version.id,
        size,
        info.content_hash,
        info.file_count,
        [{"path": p, "size": s} for p, s in info.files[:MANIFEST_MAX_ENTRIES]],
    )


def blacklisted_in_versions(versions: list[SaveVersion], blacklist: list[str], whitelist: list[str] | None = None) -> dict:
    """What the blacklist leaves in the stored versions, from their file lists (which hold up to MANIFEST_MAX_ENTRIES files: a bigger
    version may hold more, `truncated` says so): each file once, with the versions it is in and what they hold of it."""
    found: dict[str, dict] = {}
    truncated = False
    touched = 0
    for version in versions:
        hits = [f for f in version.manifest or [] if is_left_out(f["path"], blacklist, whitelist or [])]
        truncated = truncated or version.file_count > len(version.manifest or [])
        touched += bool(hits)
        for f in hits:
            entry = found.setdefault(f["path"], {"path": f["path"], "size": 0, "versions": 0})
            entry["size"] += f["size"]
            entry["versions"] += 1
    files = sorted(found.values(), key=lambda e: (-e["size"], e["path"]))
    return {"files": files, "versions": touched, "bytes": sum(e["size"] for e in files), "truncated": truncated}


def purge_blacklisted(versions: list[SaveVersion], blacklist: list[str], whitelist: list[str] | None = None) -> dict:
    """Take every blacklisted file out of the stored versions (see `remove_files`). A version too big for its file list is rewritten
    too, since the list cannot say whether it has any. Returns how many versions changed, how many went (nothing was left)
    and how many files were taken out."""
    changed = deleted = removed = 0
    if not blacklist:
        return {"versions_changed": 0, "versions_deleted": 0, "files_removed": 0}
    for version in versions:
        listed = version.manifest or []
        if not any(is_left_out(f["path"], blacklist, whitelist or []) for f in listed) and version.file_count <= len(listed):
            continue
        before = version.file_count
        after = remove_files(version, blacklist, whitelist)
        left = 0 if after is None else after.file_count
        if left == before:
            continue
        changed += after is not None
        deleted += after is None
        removed += before - left
    return {"versions_changed": changed, "versions_deleted": deleted, "files_removed": removed}
