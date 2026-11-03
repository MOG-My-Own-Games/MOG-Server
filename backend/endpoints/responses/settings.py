from __future__ import annotations

from pydantic import BaseModel, Field

from models.settings import DOWNLOAD_WORKERS_MAX, DOWNLOAD_WORKERS_MIN, SAVES_KEEP_MAX, SAVES_KEEP_MIN


class SettingsSchema(BaseModel):
    igdb_client_id: str | None
    igdb_client_secret: str | None
    steamgriddb_api_key: str | None
    # Whether each provider is on right now (a switch never touched reads as on, or the environment's choice).
    igdb_enabled: bool
    steamgriddb_enabled: bool
    hltb_enabled: bool
    # RomM is the user's own server: its address and an API token. The token is never sent back, only masked.
    romm_enabled: bool
    romm_url: str | None
    romm_token: str | None
    vndb_enabled: bool
    dlsite_enabled: bool
    steam_enabled: bool
    itch_enabled: bool
    watch_libraries: bool
    debug_logging: bool
    backup_daily: bool
    backup_saves: bool
    saves_keep_versions: int
    install_cache_ttl_days: int | None
    install_cache_max_bytes: int
    install_cache_auto_clean: bool
    install_default_proton_build: str | None
    install_default_auto_mode: bool | None
    install_default_manual_mode: bool | None
    download_workers: int | None


class SettingsValidationSchema(BaseModel):
    # None means "not configured" (nothing to validate); True/False is a
    # live pass/fail of whatever is configured.
    igdb_valid: bool | None
    steamgriddb_valid: bool | None
    romm_valid: bool | None


class SettingsUpdateForm(BaseModel):
    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    steamgriddb_api_key: str | None = None
    igdb_enabled: bool | None = None
    steamgriddb_enabled: bool | None = None
    hltb_enabled: bool | None = None
    romm_enabled: bool | None = None
    romm_url: str | None = None
    romm_token: str | None = None  # a token (rmm_...) or a pairing code, which is exchanged for its token
    vndb_enabled: bool | None = None
    dlsite_enabled: bool | None = None
    steam_enabled: bool | None = None
    itch_enabled: bool | None = None
    watch_libraries: bool | None = None
    debug_logging: bool | None = None
    backup_daily: bool | None = None
    backup_saves: bool | None = None
    saves_keep_versions: int | None = Field(default=None, ge=SAVES_KEEP_MIN, le=SAVES_KEEP_MAX)
    install_cache_ttl_days: int | None = Field(default=None, ge=0)
    install_cache_max_bytes: int | None = Field(default=None, ge=0)
    install_cache_auto_clean: bool | None = None
    install_default_proton_build: str | None = None
    install_default_auto_mode: bool | None = None
    install_default_manual_mode: bool | None = None
    download_workers: int | None = Field(default=None, ge=DOWNLOAD_WORKERS_MIN, le=DOWNLOAD_WORKERS_MAX)
