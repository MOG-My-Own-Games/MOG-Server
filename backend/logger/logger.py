from __future__ import annotations

import logging
import os
import sys

from logger.formatter import Formatter

LOGLEVEL = os.getenv("LOGLEVEL", "INFO").upper()

log = logging.getLogger("mog")
log.setLevel(LOGLEVEL)
log.propagate = False

if not log.handlers:
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(Formatter())
    log.addHandler(stdout_handler)
