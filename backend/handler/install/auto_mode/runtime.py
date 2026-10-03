# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Glue between the auto mode driver and one install session."""

from __future__ import annotations

import threading
from pathlib import Path

from handler.database import db_install_session_handler
from handler.install.manifest import read_live_manifest
from handler.notifications import notify_auto_mode_stuck
from logger.formatter import highlight as hl
from logger.logger import log

from .catalog import load_catalog
from .driver import STATUS_NEEDS_MANUAL, AutoModeDriver, make_x11_actor, make_x11_observer


def _notify_needs_manual(install_session_id: int, detail: str | None) -> None:
    """Log that auto mode is stuck and notify the user who started the install.

    Runs on the driver's own background thread.
    """
    log.warning(
        f"Install session {hl(str(install_session_id))} auto mode needs manual help: {detail}"
    )
    try:
        notify_auto_mode_stuck(install_session_id, detail)
    except Exception as e:  # noqa: BLE001 - a lost notification must not stop auto mode
        log.warning(f"Could not create the auto mode notification: {e}")


def build_driver(install_session_id: int, display: str, work_dir: Path) -> AutoModeDriver:
    def enabled() -> bool:
        session = db_install_session_handler.get_session(install_session_id)
        return bool(session and session.auto_mode)

    def progress() -> int:
        live = read_live_manifest(work_dir)
        return sum(e.size_bytes for e in live.values()) if live else 0

    def report(status: str | None, detail: str | None) -> None:
        db_install_session_handler.update_session(
            install_session_id, {"auto_status": status, "auto_detail": detail}
        )
        # `_set` on the driver's own side only calls `report` on a genuine
        # change, so this fires exactly once per transition into the state,
        # not on every tick spent stuck there.
        if status == STATUS_NEEDS_MANUAL:
            _notify_needs_manual(install_session_id, detail)

    catalog = load_catalog()
    return AutoModeDriver(
        catalog=catalog,
        observe=make_x11_observer(display, catalog),
        act=make_x11_actor(display),
        enabled=enabled,
        progress=progress,
        report=report,
    )


def start_auto_mode(
    install_session_id: int, display: str, work_dir: Path, stop: threading.Event
) -> threading.Thread:
    driver = build_driver(install_session_id, display, work_dir)
    thread = threading.Thread(target=driver.run, args=(stop,), daemon=True)
    thread.start()
    return thread
