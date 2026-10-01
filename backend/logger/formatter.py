"""Plain log formatting, plus the `highlight` helper many handler modules use
to pick out an id/name in an otherwise plain log line."""

from __future__ import annotations

import logging
import os

NO_COLOR = os.getenv("NO_COLOR") is not None

CYAN = "\033[36m"
RESET = "\033[0m"

_LEVEL_COLOR = {
    logging.DEBUG: "\033[90m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[41m",
}


def highlight(text: str) -> str:
    """Wrap `text` for emphasis inside a log message."""
    if NO_COLOR:
        return text
    return f"{CYAN}{text}{RESET}"


class Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        if not NO_COLOR:
            color = _LEVEL_COLOR.get(record.levelno, "")
            base = f"{color}%(asctime)s | %(levelname)-8s | %(name)s{RESET} | %(message)s"
        formatter = logging.Formatter(base, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)
