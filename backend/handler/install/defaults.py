"""Install-mode defaults for a start request that doesn't pass them.

The DB setting (editable from Settings in the web UI) takes priority over the
env var, same pattern as utils.install_cache.default_ttl_days.
"""

from __future__ import annotations

from config import INSTALL_AUTO_MODE_DEFAULT


def default_auto_mode() -> bool:
    from handler.database import db_settings_handler

    configured = db_settings_handler.get_settings().install_default_auto_mode
    return configured if configured is not None else INSTALL_AUTO_MODE_DEFAULT


def default_manual_mode() -> bool:
    from handler.database import db_settings_handler

    return bool(db_settings_handler.get_settings().install_default_manual_mode)
