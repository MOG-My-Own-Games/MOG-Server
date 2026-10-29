"""A limit on what the installer caches take, and what an install does when its installer does not fit.

The room for a new cache is the smaller of what is left of the limit (Settings; 0 is no limit) and the free space of the
disk holding the caches. An installer that fits starts (with a warning when it fits by less than 20%). One that does not
fit makes room by deleting the oldest caches that are not in use, while "auto clean" is on; waits in the queue when only a
running install's cache is in the way; and is refused when it could never fit. `decide` is the pure part of it."""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass
from pathlib import Path

from config import INSTALL_CACHE_PATH
from handler.database import db_game_handler, db_install_session_handler, db_settings_handler
from handler.filesystem import fs_game_handler
from handler.install.archive_prescan import is_archive_candidate
from handler.install.copy_step import is_copy_step
from logger.logger import log
from models.install_session import ACTIVE_INSTALL_STATES, InstallSession, InstallSessionState
from utils.install_cache import cache_left, cache_root_dirs, clear_session_cache, dir_size_bytes

# An installer needs its own size and then some: the install is usually a bit bigger, so a fit this tight is flagged.
HEADROOM = 0.2


class CacheTooSmall(RuntimeError):
    """The installer can never fit in the installer cache as it is set up."""


class Wait(RuntimeError):
    """The install has to wait for cache space; the message is the warning to show on it."""


class Verdict(enum.Enum):
    FITS = "fits"
    EVICT = "evict"  # fits once old caches are deleted
    WAIT = "wait"  # fits once a running install is over
    TOO_BIG = "too_big"


def limit_bytes(settings) -> int:
    return max(0, settings.install_cache_max_bytes or 0)


def auto_clean(settings) -> bool:
    return True if settings.install_cache_auto_clean is None else settings.install_cache_auto_clean


def room_after(limit: int, used: int, disk_free: int, released: int = 0) -> int:
    """What is free for a new cache once `released` of the `used` bytes are gone: the rest of the limit or the disk's free
    space, whichever is smaller (the disk alone with no limit)."""
    on_disk = disk_free + released
    return on_disk if limit <= 0 else max(0, min(limit - (used - released), on_disk))


def decide(need: int, limit: int, used: int, locked: int, disk_free: int, clean: bool, pinned: int = 0) -> Verdict:
    """What to do with an installer of `need` bytes. `locked` is the part of `used` that belongs to installs in progress
    (they end, and then it can go); `pinned` is the session's own cache, which stays whatever happens."""
    idle = used - locked - pinned
    if need <= room_after(limit, used, disk_free):
        return Verdict.FITS
    if clean and need <= room_after(limit, used, disk_free, idle):
        return Verdict.EVICT
    if locked and need <= room_after(limit, used, disk_free, locked + (idle if clean else 0)):
        return Verdict.WAIT
    return Verdict.TOO_BIG


def is_tight(need: int, room: int) -> bool:
    return need * (1 + HEADROOM) > room


def fmt(size: int) -> str:
    value, unit = float(size), "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            break
        value /= 1024
    return f"{value:.1f} {unit}"


def disk_free_bytes() -> int:
    """Free space of the disk the install caches are on (the nearest folder that exists, as the caches may not yet)."""
    path = Path(INSTALL_CACHE_PATH)
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


@dataclass(frozen=True)
class Cache:
    session_id: int | None
    size: int
    age: float  # seconds since the epoch of the last change: the smaller, the older
    busy: bool  # an install in progress writes in it: never deleted
    own: bool = False  # the cache of the session being admitted, or a folder that is not a session's: stays whatever happens


def _caches(keep_session_id: int | None = None) -> list[Cache]:
    found = []
    for path in cache_root_dirs():
        try:
            session_id = int(path.name)
        except ValueError:
            session_id = None
        session = db_install_session_handler.get_session(session_id) if session_id is not None else None
        if session is not None:
            when = session.updated_at or session.created_at
            age = when.timestamp() if when else 0.0
        else:
            try:
                age = path.stat().st_mtime
            except OSError:
                age = 0.0
        busy = session is not None and session.state in ACTIVE_INSTALL_STATES and session.state != InstallSessionState.QUEUED
        found.append(Cache(session_id, dir_size_bytes(path), age, busy, own=session_id is None or session_id == keep_session_id))
    return found


def snapshot(used: int | None = None) -> dict:
    """The numbers the web UI shows: what the caches take, the limit, and what is free of the smaller of limit and disk."""
    settings = db_settings_handler.get_settings()
    limit, disk = limit_bytes(settings), disk_free_bytes()
    taken = used if used is not None else sum(c.size for c in _caches())
    return {
        "used_bytes": taken,
        "limit_bytes": limit,
        "free_bytes": room_after(limit, taken, disk),
        "disk_free_bytes": disk,
        "auto_clean": auto_clean(settings),
    }


def estimate_installer_bytes(session: InstallSession) -> int | None:
    """About how much the install will take: the archives or disc images it unpacks, else the game's own folder, once for
    all the steps. None when it cannot be told (nothing is then checked)."""
    game = db_game_handler.get_game(session.game_id)
    if game is None:
        return None
    pairs = [(s.get("path"), s.get("source_path")) for s in session.steps] if session.steps else [(session.installer_path, session.source_path)]
    total, whole_game = 0, False
    for installer, source in pairs:
        named = source
        try:
            if named is None and installer and not is_copy_step(installer):
                if is_archive_candidate(Path(fs_game_handler.resolve_installer_abs_path(game, installer))):
                    named = installer
            if named is None:
                whole_game = True
            else:
                total += Path(fs_game_handler.resolve_installer_abs_path(game, named)).stat().st_size
        except (OSError, ValueError):
            whole_game = True
    if whole_game and game.size_bytes:
        total += game.size_bytes
    return total or None


def _evict(victims: list[Cache]) -> int:
    freed = 0
    for cache in victims:
        if cache.session_id is None:
            continue
        clear_session_cache(cache.session_id)
        if cache_left(cache.session_id):
            continue  # nothing was freed, and it is not expired as far as the disk goes
        if db_install_session_handler.get_session(cache.session_id) is not None:
            db_install_session_handler.update_session(cache.session_id, {"state": InstallSessionState.EXPIRED})
        freed += cache.size
    return freed


def admit(session: InstallSession) -> str | None:
    """Make room for the session's install. Returns the warning to show on it (None when there is none). Raises Wait (the
    warning to show) when it must wait for a running install, and CacheTooSmall when it can never fit."""
    try:
        need = estimate_installer_bytes(session)
    except Exception as e:  # noqa: BLE001 - a size that cannot be told never keeps an install from starting
        log.debug(f"Install cache for session {session.id}: could not tell the installer's size: {e}")
        return None
    if need is None:
        return None
    settings = db_settings_handler.get_settings()
    limit, clean = limit_bytes(settings), auto_clean(settings)
    caches = _caches(keep_session_id=session.id)
    used, disk = sum(c.size for c in caches), disk_free_bytes()
    locked = sum(c.size for c in caches if c.busy and not c.own)
    pinned = sum(c.size for c in caches if c.own)
    verdict = decide(need, limit, used, locked, disk, clean, pinned)
    log.debug(
        f"Install cache for session {session.id}: needs {need}, limit {limit}, used {used} (in use {locked}, own {pinned}), "
        f"disk free {disk}: {verdict.value}"
    )
    room = room_after(limit, used, disk)
    if verdict is Verdict.TOO_BIG:
        idle = used - locked - pinned
        best = room_after(limit, used, disk, idle if clean else 0)
        raise CacheTooSmall(
            f"The installer ({fmt(need)}) does not fit in the installer cache: {fmt(best)} could be made free"
            + (f" of a {fmt(limit)} limit" if limit else "")
            + (". Raise the limit or free disk space." if clean else ". Clear some caches, raise the limit, free disk space or turn automatic cleaning on.")
        )
    if verdict is Verdict.WAIT:
        raise Wait(
            f"Waiting for cache space: the installer ({fmt(need)}) needs more than the {fmt(room)} free, and the rest is held by "
            "installs in progress. It starts when they are over"
            + (" (older caches, perhaps recent ones, are then deleted to make room)." if clean else ".")
        )
    if verdict is Verdict.EVICT:
        victims, released = [], 0
        for cache in sorted((c for c in caches if not c.busy and not c.own), key=lambda c: c.age):
            if need <= room_after(limit, used, disk, released):
                break
            victims.append(cache)
            released += cache.size
        freed = _evict(victims)
        log.info(f"Install cache: {len(victims)} old cache(s) deleted ({fmt(freed)}) to make room for session {session.id}")
        return f"{len(victims)} older install cache{'s' if len(victims) != 1 else ''} ({fmt(freed)}) deleted to make room for this installer."
    if is_tight(need, room):
        return f"The installer cache is tight: the installer ({fmt(need)}) plus 20% is more than the {fmt(room)} free."
    return None


def run_warning(session: InstallSession, warning: str) -> None:
    """The warning as a notification for the session's user; never fails the install."""
    from handler.notifications import notify_install_cache

    try:
        notify_install_cache(session.id, warning)
    except Exception as e:  # noqa: BLE001 - a failed notification must not stop an install
        log.warning(f"Could not create the install cache notification: {e}")

