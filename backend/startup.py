from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config import INSTALL_CACHE_PATH, MOG_BASE_PATH, PROTON_INSTALL_ROOT, RESOURCES_BASE_PATH
from handler.auth import hash_password
from handler.database import db_user_handler
from logger.logger import log
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


def run_startup_tasks() -> None:
    _ensure_dirs()
    _run_migrations()
    _ensure_default_admin()
