"""Install-mode defaults for a start request that doesn't pass them.

The DB setting (editable from Settings in the web UI) takes priority over the
env var, same pattern as utils.install_cache.default_ttl_days.
"""

from __future__ import annotations

from config import INSTALL_AUTO_MODE_DEFAULT, INSTALL_DOWNLOAD_WORKERS_DEFAULT
from models.settings import DOWNLOAD_WORKERS_MAX, DOWNLOAD_WORKERS_MIN


def default_auto_mode() -> bool:
    from handler.database import db_settings_handler

    configured = db_settings_handler.get_settings().install_default_auto_mode
    return configured if configured is not None else INSTALL_AUTO_MODE_DEFAULT


def default_manual_mode() -> bool:
    from handler.database import db_settings_handler

    return bool(db_settings_handler.get_settings().install_default_manual_mode)


def default_download_workers() -> int:
    """How many files a client should download at once, kept within the allowed range."""
    from handler.database import db_settings_handler

    configured = db_settings_handler.get_settings().download_workers
    value = configured if configured is not None else INSTALL_DOWNLOAD_WORKERS_DEFAULT
    return min(max(value, DOWNLOAD_WORKERS_MIN), DOWNLOAD_WORKERS_MAX)
