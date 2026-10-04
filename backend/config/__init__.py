"""Environment configuration. Single source: env vars (+ .env), no runtime-editable
layer - keeping the self-host story to "one file you edit and restart" on purpose.
"""

from __future__ import annotations

import os
from typing import Final

from dotenv import load_dotenv

load_dotenv()


def _get_env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def safe_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def safe_str_to_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


IS_PYTEST_RUN: Final[bool] = _get_env("PYTEST_VERSION") is not None

DEV_MODE: Final[bool] = safe_str_to_bool(_get_env("DEV_MODE"))
MOG_VERSION: Final[str] = _get_env("MOG_VERSION", "dev")  # type: ignore[assignment]

MOG_BASE_PATH: Final[str] = _get_env("MOG_BASE_PATH") or "/mog"
RESOURCES_BASE_PATH: Final[str] = f"{MOG_BASE_PATH}/resources"

SQLITE_PATH: Final[str] = _get_env("SQLITE_PATH") or f"{MOG_BASE_PATH}/mog.db"

# --- Save sync ---

# Next to the database, in the persistent data volume. Never under RESOURCES_BASE_PATH: that
# directory is served without authentication.
SAVES_BASE_PATH: Final[str] = f"{MOG_BASE_PATH}/saves"

# Versions kept per (user, game, device); the oldest are dropped on upload.
SAVES_KEEP_VERSIONS: Final[int] = min(20, max(1, safe_int(_get_env("SAVES_KEEP_VERSIONS"), 3)))

MAX_SAVE_UPLOAD_BYTES: Final[int] = max(1, safe_int(_get_env("MAX_SAVE_UPLOAD_BYTES"), 512 * 1024 * 1024))

AUTH_SECRET_KEY: Final[str] = _get_env("AUTH_SECRET_KEY") or "dev-insecure-secret-key"

# --- Install sandbox ---

INSTALL_CACHE_PATH: Final[str] = f"{MOG_BASE_PATH}/cache/installs"

INSTALL_CACHE_DEFAULT_TTL_DAYS: Final[int] = safe_int(
    _get_env("INSTALL_CACHE_DEFAULT_TTL_DAYS"), 7
)

INSTALL_MAX_CONCURRENCY: Final[int] = max(
    1, safe_int(_get_env("INSTALL_MAX_CONCURRENCY"), 1)
)

INSTALL_TIMEOUT: Final[int] = safe_int(_get_env("INSTALL_TIMEOUT"), 3600)  # 1 hour

INSTALL_SANDBOX_ENABLED: Final[bool] = safe_str_to_bool(
    _get_env("INSTALL_SANDBOX_ENABLED") or "true"
)

INSTALL_VNC_PORT_MIN: Final[int] = safe_int(_get_env("INSTALL_VNC_PORT_MIN"), 6900)
INSTALL_VNC_PORT_MAX: Final[int] = safe_int(_get_env("INSTALL_VNC_PORT_MAX"), 6999)

# Builds extracted to PROTON_INSTALL_ROOT/<build_id>/ on first use, not baked
# into the image.
PROTON_INSTALL_ROOT: Final[str] = _get_env("PROTON_INSTALL_ROOT") or "/opt/proton"

INSTALL_DEFAULT_PROTON_BUILD: Final[str | None] = _get_env("INSTALL_DEFAULT_PROTON_BUILD")

INSTALL_AUTO_OCR_LANGS: Final[str] = (
    _get_env("INSTALL_AUTO_OCR_LANGS") or "eng+ita+deu+fra+spa+jpn+chi_sim+chi_tra"
)
INSTALL_AUTO_OCR_DEEP_LANGS: Final[str] = _get_env("INSTALL_AUTO_OCR_DEEP_LANGS") or "eng+ita"

INSTALL_AUTO_STUCK_SECONDS: Final[int] = max(
    10, safe_int(_get_env("INSTALL_AUTO_STUCK_SECONDS"), 60)
)

INSTALL_DOWNLOAD_WORKERS_DEFAULT: Final[int] = safe_int(_get_env("INSTALL_DOWNLOAD_WORKERS"), 4)
INSTALL_AUTO_MODE_DEFAULT: Final[bool] = safe_str_to_bool(_get_env("INSTALL_AUTO_MODE_DEFAULT") or "true")

# --- Metadata providers ---

IGDB_CLIENT_ID: Final[str | None] = _get_env("IGDB_CLIENT_ID")
IGDB_CLIENT_SECRET: Final[str | None] = _get_env("IGDB_CLIENT_SECRET")
STEAMGRIDDB_API_KEY: Final[str | None] = _get_env("STEAMGRIDDB_API_KEY")
