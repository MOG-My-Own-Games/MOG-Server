"""The log level, switched from Settings without a restart."""

from __future__ import annotations

import logging

from logger.logger import LOGLEVEL, log


def enabled_in(settings) -> bool:
    """Whether DEBUG lines are logged: the switch in Settings, or, while it has not been touched, LOGLEVEL=DEBUG."""
    return LOGLEVEL == "DEBUG" if settings.debug_logging is None else settings.debug_logging


def apply(settings) -> None:
    if enabled_in(settings):
        log.setLevel(logging.DEBUG)
    else:
        # LOGLEVEL=DEBUG in the environment is only the default of a switch that was turned off.
        log.setLevel(logging.INFO if LOGLEVEL == "DEBUG" else LOGLEVEL)