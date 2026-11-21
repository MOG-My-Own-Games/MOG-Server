"""The switch for gamepad support (Settings > Controller support): whether the support Steam declares is looked up for the games and
handed out with them. Off, nothing is asked of Steam for it and the games go out with none; what was kept is not lost."""

from __future__ import annotations

from handler.database import db_settings_handler


def enabled_in(settings) -> bool:
    return settings.controller_support_enabled is not False


def is_enabled() -> bool:
    return enabled_in(db_settings_handler.get_settings())
