from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path

from config import (
    INSTALL_CACHE_PATH,
    MOG_BASE_PATH,
    PROTON_INSTALL_ROOT,
    RESOURCES_BASE_PATH,
    SAVES_BASE_PATH,
)
from handler.auth import hash_password, verify_password
from handler import backups, log_level
from handler.database import db_install_session_handler, db_settings_handler, db_user_handler
from handler.install.runner import dispatch_queue
from logger.logger import log
from models.install_session import InstallSessionState
from models.user import Role, User


def _ensure_dirs() -> None:
    for path in (
        MOG_BASE_PATH,
        RESOURCES_BASE_PATH,
        SAVES_BASE_PATH,
        INSTALL_CACHE_PATH,
        PROTON_INSTALL_ROOT,
    ):
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # The Dockerfile pre-creates and chowns PROTON_INSTALL_ROOT, so this
            # only fires for an unusual deployment (a read-only or externally
            # managed mount) - log it rather than crashing the whole server over
            # a directory it may not actually need to create itself.
            log.warning(f"Could not create {path}: {e}")


def _remove_public_image_cache() -> None:
    """Provider pictures used to be cached under the resources folder, which is served without authentication."""
    shutil.rmtree(Path(RESOURCES_BASE_PATH) / "imagecache", ignore_errors=True)


def _run_migrations() -> None:
    backups.before_update()  # the database is copied first when this start has migrations to run
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=Path(__file__).parent)


# What env.template and the usual examples say: a password that anybody can guess, so it is never used.
PLACEHOLDER_PASSWORDS = frozenset({"change-me", "changeme", "mog-admin", "admin", "password"})


def _ensure_default_admin() -> None:
    """Create the admin from env vars if the users table is empty. After that,
    MOG_ADMIN_PASSWORD (when set) is the source of truth for that account's
    password: a changed value is rewritten to the DB on every boot.

    A missing password, or a placeholder from the template, is not used: the first admin gets a random one, shown once
    in the log, and an existing account keeps the password it has."""
    username = os.getenv("MOG_ADMIN_USERNAME", "admin")
    env_password = os.getenv("MOG_ADMIN_PASSWORD")
    placeholder = bool(env_password) and env_password.strip().lower() in PLACEHOLDER_PASSWORDS
    if placeholder:
        log.warning("MOG_ADMIN_PASSWORD is still a placeholder: it is ignored. Set a password of your own.")
        env_password = None
    if not db_user_handler.get_all_users():
        generated = env_password is None
        password = env_password or secrets.token_urlsafe(16)
        db_user_handler.add_user(
            User(
                username=username,
                hashed_password=hash_password(password),
                role=Role.ADMIN,
                enabled=True,
            )
        )
        if generated:
            log.warning(f"Created admin user {username!r} with the password {password!r}: shown only now, change it.")
        else:
            log.warning(f"Created admin user {username!r}.")
        return

    admin = db_user_handler.get_user_by_username(username)
    if env_password and admin and not verify_password(env_password, admin.hashed_password):
        db_user_handler.update_user(admin.id, {"hashed_password": hash_password(env_password)})
        log.warning(f"Password of {username!r} reset from MOG_ADMIN_PASSWORD.")


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
    _remove_public_image_cache()
    _run_migrations()
    _ensure_default_admin()
    log_level.apply(db_settings_handler.get_settings())
    _fail_orphaned_installs()
    dispatch_queue()  # what was waiting for a place before the restart still waits its turn
