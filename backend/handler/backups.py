"""Backups of what only this server has: the database, the avatars and uploaded artwork, and (when asked) the saves.

Made as one `.tar.gz` in the backup volume (BACKUP_PATH, a volume of its own): `daily` (from BACKUP_HOUR, once a day, while the
switch in Settings is on), `pre-update` (the database alone, before a start that has migrations to run) and `manual` (the button
in Settings). The database is copied with SQLite's own backup, so it is whole even while the server writes to it. What is
regenerated (the install caches, the image cache) is not in them. The newest few of each kind are kept. To restore, stop the
server and put `mog.db` (and the folders) of the archive back in the data volume; see docs/BACKUPS.md."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from config import (
    BACKUP_HOUR,
    BACKUP_KEEP_DAILY,
    BACKUP_KEEP_MANUAL,
    BACKUP_KEEP_PRE_UPDATE,
    ADDONS_PATH,
    BACKUP_PATH,
    MOG_VERSION,
    RESOURCES_BASE_PATH,
    SAVES_BASE_PATH,
    SQLITE_PATH,
)
from logger.logger import log

DAILY, PRE_UPDATE, MANUAL = "daily", "pre-update", "manual"
KINDS = (DAILY, PRE_UPDATE, MANUAL)
KEEP = {DAILY: BACKUP_KEEP_DAILY, PRE_UPDATE: BACKUP_KEEP_PRE_UPDATE, MANUAL: BACKUP_KEEP_MANUAL}
_NAME = re.compile(r"mog-(?P<kind>daily|pre-update|manual)-(?P<stamp>\d{8}-\d{6})(?P<saves>-saves)?\.tar\.gz")
CHECK_EVERY = 60.0  # seconds between the scheduler's looks
# Folders of the resources that hold what a person put there; the image cache is regenerated and is left out.
RESOURCE_FOLDERS = ("avatars", "media")

_lock = threading.Lock()
last_error: str | None = None


class BackupUnavailable(RuntimeError):
    """There is no backup volume to write to."""


class BackupBusy(RuntimeError):
    """Another backup is being made."""


def backup_dir() -> Path:
    return Path(BACKUP_PATH)


def available() -> str | None:
    """None when backups can be made, else why not."""
    folder = backup_dir()
    if not folder.is_dir():
        return f"No backup volume: {folder} does not exist. Mount a volume there."
    if not os.access(folder, os.W_OK):
        return f"The backup volume {folder} is not writable by the server."
    return None


def is_own_volume() -> bool:
    """Whether the backup folder is a mount of its own (a volume), not a folder inside the server's own storage."""
    return os.path.ismount(backup_dir())


def free_bytes() -> int | None:
    try:
        return shutil.disk_usage(backup_dir()).free
    except OSError:
        return None


def daily_enabled(settings) -> bool:
    return True if settings.backup_daily is None else settings.backup_daily


def saves_in_daily(settings) -> bool:
    return False if settings.backup_saves is None else settings.backup_saves


def _describe(path: Path) -> dict | None:
    match = _NAME.fullmatch(path.name)
    if match is None or not path.is_file():
        return None
    stat = path.stat()
    return {
        "name": path.name,
        "kind": match["kind"],
        "created_at": datetime.strptime(match["stamp"], "%Y%m%d-%H%M%S").astimezone(),
        "size_bytes": stat.st_size,
        "includes_saves": bool(match["saves"]),
    }


def list_backups() -> list[dict]:
    """What the backup volume holds, newest first."""
    try:
        found = [d for d in (_describe(p) for p in backup_dir().iterdir()) if d]
    except OSError:
        return []
    return sorted(found, key=lambda d: d["created_at"], reverse=True)


def path_of(name: str) -> Path | None:
    """The file of a backup by its name, or None when it is not one (nothing else in the folder is reachable this way)."""
    return backup_dir() / name if _NAME.fullmatch(name) and (backup_dir() / name).is_file() else None


def delete(name: str) -> bool:
    path = path_of(name)
    if path is None:
        return False
    path.unlink()
    log.info(f"Backup {name} deleted")
    return True


def prune(kind: str) -> list[str]:
    """Drop the oldest backups of `kind` beyond how many are kept; returns their names."""
    dropped = []
    for old in [d for d in list_backups() if d["kind"] == kind][KEEP[kind] :]:
        (backup_dir() / old["name"]).unlink(missing_ok=True)
        dropped.append(old["name"])
    if dropped:
        log.info(f"Backups pruned ({kind}, keeping {KEEP[kind]}): {', '.join(dropped)}")
    return dropped


def _copy_database(target: Path) -> None:
    """A whole copy of the database through SQLite's backup, correct whatever is being written meanwhile."""
    source = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _revision() -> str | None:
    try:
        with sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True) as db:
            row = db.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def create(kind: str, include_saves: bool = False, note: str | None = None, now: datetime | None = None) -> dict:
    """Make a backup of `kind` in the backup volume and prune the old ones. Raises BackupUnavailable (no volume) or BackupBusy."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind of backup: {kind}")
    why = available()
    if why:
        raise BackupUnavailable(why)
    if not _lock.acquire(blocking=False):
        raise BackupBusy("Another backup is being made")
    started = time.monotonic()
    when = now or datetime.now()
    name = f"mog-{kind}-{when:%Y%m%d-%H%M%S}{'-saves' if include_saves else ''}.tar.gz"
    final, part = backup_dir() / name, backup_dir() / f".{name}.part"
    try:
        with tempfile.TemporaryDirectory(dir=backup_dir(), prefix=".work-") as work:
            database = Path(work) / "mog.db"
            _copy_database(database)
            manifest = {
                "kind": kind,
                "created_at": when.isoformat(timespec="seconds"),
                "mog_version": MOG_VERSION,
                "database_revision": _revision(),
                "includes_saves": include_saves,
                "note": note,
            }
            (Path(work) / "manifest.json").write_text(json.dumps(manifest, indent=2))
            with tarfile.open(part, "w:gz") as tar:
                tar.add(database, arcname="mog.db")
                tar.add(Path(work) / "manifest.json", arcname="manifest.json")
                if kind != PRE_UPDATE:  # before an update only the database is at stake, and it must be quick
                    for folder in RESOURCE_FOLDERS:
                        source = Path(RESOURCES_BASE_PATH) / folder
                        if source.is_dir():
                            tar.add(source, arcname=f"resources/{folder}")
                    # Mods, patches and DLC kept from installs: made once, not again, so they are never left out.
                    if Path(ADDONS_PATH).is_dir():
                        tar.add(ADDONS_PATH, arcname="addons")
                    if include_saves and Path(SAVES_BASE_PATH).is_dir():
                        tar.add(SAVES_BASE_PATH, arcname="saves")
        part.replace(final)
    except BaseException as e:
        part.unlink(missing_ok=True)
        _remember(f"{kind} backup failed: {e}")
        raise
    finally:
        _lock.release()
    _remember(None)
    info = _describe(final)
    log.info(f"Backup {name} made ({info['size_bytes']:,} bytes, {time.monotonic() - started:.1f}s)")
    prune(kind)
    return info


def _remember(error: str | None) -> None:
    global last_error
    last_error = error


def pending_migration() -> bool:
    """Whether the database exists and is at an older revision than the code's: the start is about to migrate it."""
    if not Path(SQLITE_PATH).is_file() or Path(SQLITE_PATH).stat().st_size == 0:
        return False
    current = _revision()
    if current is None:
        return False  # not a database MOG has migrated: nothing to protect yet
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parent.parent / "alembic"))
    return current != ScriptDirectory.from_config(config).get_current_head()


def before_update() -> None:
    """The database alone, before a start that migrates it. Never stops the start: with no volume, or on a failure, it says so."""
    try:
        if not pending_migration():
            return
        create(PRE_UPDATE, note=f"before the update to {MOG_VERSION}, from revision {_revision()}")
    except BackupUnavailable as e:
        log.warning(f"The database is migrating and was not backed up first: {e}")
    except Exception as e:  # noqa: BLE001 - a backup that fails must not keep the server from starting
        log.error(f"The backup before the update failed, the update goes on: {e}")


def daily_due(now: datetime | None = None, existing: list[dict] | None = None) -> bool:
    """Whether today's daily backup is still to be made: it is past BACKUP_HOUR and none was made today."""
    now = now or datetime.now()
    if now.hour < BACKUP_HOUR:
        return False
    today = now.date()
    return not any(d["kind"] == DAILY and d["created_at"].date() == today for d in (existing if existing is not None else list_backups()))


def run(stop: threading.Event, settings_getter) -> None:
    """The scheduler: looks every minute and makes the daily backup when it is due and the switch in Settings is on."""
    while not stop.wait(CHECK_EVERY):
        try:
            settings = settings_getter()
            if daily_enabled(settings) and available() is None and daily_due():
                create(DAILY, include_saves=saves_in_daily(settings))
        except BackupBusy:
            continue
        except Exception as e:  # noqa: BLE001 - the thread must outlive a failed backup
            log.error(f"The daily backup failed: {e}")


def start() -> threading.Event:
    from handler.database import db_settings_handler

    stop = threading.Event()
    threading.Thread(target=run, args=(stop, db_settings_handler.get_settings), daemon=True, name="backups").start()
    log.info(f"Daily backups are made from {BACKUP_HOUR:02d}:00 into {BACKUP_PATH} while they are on in Settings")
    return stop
