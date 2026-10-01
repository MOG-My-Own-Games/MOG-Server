# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import INSTALL_CACHE_DEFAULT_TTL_DAYS, INSTALL_CACHE_PATH
from logger.formatter import highlight as hl
from logger.logger import log

SECONDS_PER_DAY = 86400

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


def session_cache_dir(session_id: int) -> Path:
    """Absolute working directory for a single install session's files."""
    return Path(INSTALL_CACHE_PATH) / str(session_id)


def ensure_session_cache_dir(session_id: int) -> Path:
    """Create and return the session's cache directory."""
    path = session_cache_dir(session_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def clear_session_cache(session_id: int) -> bool:
    """Remove a session's cached files. Returns True if anything was removed."""
    path = session_cache_dir(session_id)
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    log.info(f"Cleared install cache for session {hl(str(session_id))}")
    return True


def dir_size_bytes(path: Path) -> int:
    """Size on disk of a directory tree. Hardlinks (the live install loop
    links files into the cache root) are counted once."""
    if not path.exists():
        return 0
    seen: set[tuple[int, int]] = set()
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_symlink() or not f.is_file():
                continue
            st = f.stat()
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

    expired = db_install_session_handler.get_expired_sessions()
    for session in expired:
        clear_session_cache(session.id)
        db_install_session_handler.update_session(session.id, {"state": InstallSessionState.EXPIRED})

    if expired:
        log.info(f"Evicted {hl(str(len(expired)))} expired install caches")
    return len(expired)
