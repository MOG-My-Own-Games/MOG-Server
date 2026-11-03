# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from __future__ import annotations

import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import INSTALL_CACHE_DEFAULT_TTL_DAYS, INSTALL_CACHE_PATH
from logger.formatter import highlight as hl
from logger.logger import log

SECONDS_PER_DAY = 86400
# How often the expired caches are looked for. The TTL is in days, so an hour late is nothing.
CLEANUP_EVERY = 3600.0
# A folder of the cache with no session is a leftover; one this young may still be being made.
ORPHAN_GRACE = SECONDS_PER_DAY

# TTL sentinels for the API surface. A positive value is seconds-from-now,
# ``UNLIMITED_TTL`` maps to a NULL ``expires_at`` (never auto-evicted).
UNLIMITED_TTL = -1


def default_ttl_days() -> int:
    """The configured default TTL in days - the DB setting (editable from
    Settings in the web UI) takes priority over the env var, which stays as
    the bootstrap/.env-only fallback."""
    from handler.database import db_settings_handler

    configured = db_settings_handler.get_settings().install_cache_ttl_days
    return configured if configured is not None else INSTALL_CACHE_DEFAULT_TTL_DAYS


def resolve_expires_at(ttl_seconds: int | None) -> datetime | None:
    """Translate a requested TTL into an absolute ``expires_at``.

    ``None`` uses the configured default TTL (in days; 0 is unlimited),
    ``UNLIMITED_TTL`` (or any value <= 0 other than the default request)
    yields ``None`` (unlimited).
    """
    if ttl_seconds is None:
        ttl_seconds = default_ttl_days() * SECONDS_PER_DAY
    if ttl_seconds <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


def apply_default_ttl() -> int:
    """Put the default TTL on the caches that never expire. A default of 0 leaves them be."""
    from handler.database import db_install_session_handler

    expires_at = resolve_expires_at(None)
    if expires_at is None:
        return 0
    return db_install_session_handler.set_expiry_where_unlimited(expires_at)


def session_cache_dir(session_id: int) -> Path:
    """Absolute working directory for a single install session's files."""
    return Path(INSTALL_CACHE_PATH) / str(session_id)


def ensure_session_cache_dir(session_id: int) -> Path:
    """Create and return the session's cache directory."""
    path = session_cache_dir(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_left(session_id: int) -> bool:
    """Whether the session's cache is still on disk, which is what a clear that could not remove everything leaves."""
    return session_cache_dir(session_id).exists()


def _first_left(path: Path) -> str:
    """What a failed removal left, for the log: the first file or folder still there and who owns it."""
    for found in path.rglob("*"):
        try:
            return f"{found} is owned by uid {found.lstat().st_uid} and the server runs as uid {os.getuid()}"
        except OSError:
            continue
    return f"{path} itself could not be removed"


def clear_session_cache(session_id: int) -> bool:
    """Remove a session's cached files. Returns True if there was a cache to remove. Files this process may not delete (an
    install made while the server ran as root) stay; that is logged and `cache_left` tells, so a caller never forgets
    a cache that is still on disk."""
    path = session_cache_dir(session_id)
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        log.warning(f"Could not remove all of the install cache of session {hl(str(session_id))}: {_first_left(path)}")
    else:
        log.info(f"Cleared install cache for session {hl(str(session_id))}")
    return True


def dir_size_bytes(path: Path) -> int:
    """Size on disk of a directory tree. Hardlinks (the live install loop
    links files into the cache root) are counted once; symbolic links are not followed.
    A cache has tens of thousands of files: scandir gives each one's type with the listing, so it takes one stat per file."""
    if not path.exists():
        return 0
    seen: set[tuple[int, int]] = set()
    total = 0
    pending = [str(path)]
    while pending:
        try:
            listing = os.scandir(pending.pop())
        except OSError:
            continue
        with listing:
            for entry in listing:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                total += st.st_size
    return total


def cache_size_bytes(session_id: int) -> int:
    """Total size on disk of a session's cached files."""
    return dir_size_bytes(session_cache_dir(session_id))


def cache_root_dirs() -> list[Path]:
    """Every per-session directory currently under the install cache root."""
    root = Path(INSTALL_CACHE_PATH)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def purge_superseded_sessions(
    game_id: int, keep_session_id: int, owner_user_id: int, only_states: frozenset | None = None
) -> int:
    """Delete the other sessions (and their caches) of a game, so it keeps at
    most one install cache per user. Running sessions, and sessions owned by
    a different user, are never touched; `only_states` narrows which of the
    rest are removed."""
    from handler.database import db_install_session_handler
    from models.install_session import RUNNING_INSTALL_STATES

    removed = 0
    for old in db_install_session_handler.get_sessions_for_game(game_id):
        if old.id == keep_session_id or old.state in RUNNING_INSTALL_STATES or old.user_id != owner_user_id:
            continue
        if only_states is not None and old.state not in only_states:
            continue
        clear_session_cache(old.id)
        if cache_left(old.id):
            continue  # forgetting the session would leave its files behind with no name
        db_install_session_handler.delete_session(old.id)
        removed += 1
    return removed


def cleanup_expired_installs() -> int:
    """Evict install caches whose TTL has elapsed and mark them EXPIRED.

    Unlimited sessions (``expires_at IS NULL``) are never evicted. Returns the
    number of sessions cleaned up.
    """
    from handler.database import db_install_session_handler
    from models.install_session import InstallSessionState

    cleaned = 0
    for session in db_install_session_handler.get_expired_sessions():
        clear_session_cache(session.id)
        if cache_left(session.id):
            continue  # not expired as far as the disk goes: tried again at the next look
        db_install_session_handler.update_session(session.id, {"state": InstallSessionState.EXPIRED})
        cleaned += 1

    if cleaned:
        log.info(f"Evicted {hl(str(cleaned))} expired install caches")
    return cleaned


def remove_orphan_caches(grace: float = ORPHAN_GRACE) -> int:
    """Delete the cache folders no session owns (the "?" rows of the cache list): what is left when a session was forgotten
    but its files could not be removed. Returns how many are gone."""
    from handler.database import db_install_session_handler

    removed = 0
    for path in cache_root_dirs():
        try:
            session_id = int(path.name)
            if time.time() - path.stat().st_mtime < grace:
                continue
        except (ValueError, OSError):
            continue
        if db_install_session_handler.get_session(session_id) is not None:
            continue
        clear_session_cache(session_id)
        if not cache_left(session_id):
            removed += 1
    if removed:
        log.info(f"Removed {hl(str(removed))} install cache folders no session owned")
    return removed


def sweep_install_cache() -> int:
    """One look at the caches: the expired ones and the ones nobody owns go. Returns how many folders were freed."""
    return cleanup_expired_installs() + remove_orphan_caches()


def _run_cleanup(stop: threading.Event) -> None:
    from handler.install.runner import dispatch_queue

    while True:
        try:
            if sweep_install_cache():
                dispatch_queue()  # an install may have been waiting for the room
        except Exception as e:  # noqa: BLE001 - the thread must outlive a failed look
            log.error(f"Cleaning the install cache failed: {e}")
        if stop.wait(CLEANUP_EVERY):
            return


def start_cleanup() -> threading.Event:
    """Look for expired caches at start and then every hour (nothing else evicts them)."""
    stop = threading.Event()
    threading.Thread(target=_run_cleanup, args=(stop,), daemon=True, name="install-cache-cleanup").start()
    return stop
