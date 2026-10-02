from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config import INSTALL_CACHE_PATH, MOG_BASE_PATH, PROTON_INSTALL_ROOT, RESOURCES_BASE_PATH
from handler.auth import hash_password
from handler.database import db_install_session_handler, db_user_handler
from logger.logger import log
from models.install_session import InstallSessionState
from models.user import Role, User


def _ensure_dirs() -> None:
    for path in (MOG_BASE_PATH, RESOURCES_BASE_PATH, INSTALL_CACHE_PATH, PROTON_INSTALL_ROOT):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # The Dockerfile pre-creates and chowns PROTON_INSTALL_ROOT, so this
            # only fires for an unusual deployment (a read-only or externally
            # managed mount) - log it rather than crashing the whole server over
            # a directory it may not actually need to create itself.
            log.warning(f"Could not create {path}: {e}")


def _run_migrations() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=Path(__file__).parent)


def _ensure_default_admin() -> None:
    """First-boot convenience: create an admin from env vars if the users
    table is empty. A production deployment should change the password
    immediately (no forced-reset flow yet, see docs/TODO.md)."""
    if db_user_handler.get_all_users():
        return
    username = os.getenv("MOG_ADMIN_USERNAME", "admin")
    password = os.getenv("MOG_ADMIN_PASSWORD", "mog-admin")
    db_user_handler.add_user(
        User(username=username, hashed_password=hash_password(password), role=Role.ADMIN, enabled=True)
    )
    log.warning(f"Created default admin user {username!r} - change its password (MOG_ADMIN_PASSWORD).")


def _fail_orphaned_installs() -> None:
    """Installs run inside this process, so any session still marked running
    at boot lost its installer to the restart and would sit there forever."""
    for install_session in db_install_session_handler.get_installing_sessions():
        db_install_session_handler.update_session(
            install_session.id,
            {
                "state": InstallSessionState.FAILED,
                "error": "The server restarted while this install was running",
                "vnc_url": None,
                "vnc_web_port": None,
                "vnc_token": None,
                "phase": None,
                "phase_detail": None,
                "auto_status": None,
                "auto_detail": None,
            },
        )
        log.warning(f"Install session {install_session.id} was interrupted by a restart, marked failed")


def run_startup_tasks() -> None:
    _ensure_dirs()
    _run_migrations()
    _ensure_default_admin()
    _fail_orphaned_installs()
