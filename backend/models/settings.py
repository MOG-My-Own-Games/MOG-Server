"""Runtime-editable settings: metadata provider API keys and the install
cache TTL default.

Everything else in MOG stays env-vars-only (see CLAUDE.md's architecture
notes) - these are the exception because they need to be settable from the
web UI without a container restart or shell access to edit .env. A single
row (id=1); the matching env vars remain the fallback when a field here is
empty/null, so a fresh install still works from .env alone until someone
sets it through Settings.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

SETTINGS_ROW_ID = 1

# Files a client downloads at once (see handler/install/defaults.py).
DOWNLOAD_WORKERS_MIN, DOWNLOAD_WORKERS_MAX = 1, 16


class Settings(BaseModel):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=SETTINGS_ROW_ID)
    igdb_client_id: Mapped[str | None] = mapped_column(String(length=255), default=None)
    igdb_client_secret: Mapped[str | None] = mapped_column(String(length=255), default=None)
    steamgriddb_api_key: Mapped[str | None] = mapped_column(String(length=255), default=None)
    # Days an install cache survives before auto-eviction. NULL falls back to
    # config.INSTALL_CACHE_DEFAULT_TTL_DAYS; 0 means unlimited (see
    # utils/install_cache.py's resolve_expires_at).
    install_cache_ttl_days: Mapped[int | None] = mapped_column(Integer, default=None)
    # Proton/Wine build id used when a session doesn't request one explicitly.
    # NULL falls back to config.INSTALL_DEFAULT_PROTON_BUILD (see
    # handler/install/proton_builds.py's default_build_id).
    install_default_proton_build: Mapped[str | None] = mapped_column(String(length=255), default=None)
    # Install-mode defaults for a session that doesn't say (no auto_mode /
    # manual_mode in the start request). NULL falls back to
    # config.INSTALL_AUTO_MODE_DEFAULT for auto mode and to off for manual
    # mode (see handler/install/defaults.py).
    install_default_auto_mode: Mapped[bool | None] = mapped_column(Boolean, default=None)
    install_default_manual_mode: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # Files a client downloads in parallel. NULL falls back to
    # config.INSTALL_DOWNLOAD_WORKERS_DEFAULT.
    download_workers: Mapped[int | None] = mapped_column(Integer, default=None)
