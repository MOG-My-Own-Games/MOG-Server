from __future__ import annotations

from pydantic import BaseModel


class SettingsSchema(BaseModel):
    igdb_client_id: str | None
    igdb_client_secret: str | None
    steamgriddb_api_key: str | None
    install_cache_ttl_days: int | None
    install_default_proton_build: str | None
    install_default_auto_mode: bool | None
    install_default_manual_mode: bool | None


class SettingsValidationSchema(BaseModel):
    # None means "not configured" (nothing to validate); True/False is a
    # live pass/fail of whatever is configured.
    igdb_valid: bool | None
    steamgriddb_valid: bool | None


class SettingsUpdateForm(BaseModel):
    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    steamgriddb_api_key: str | None = None
    install_cache_ttl_days: int | None = None
    install_default_proton_build: str | None = None
    install_default_auto_mode: bool | None = None
    install_default_manual_mode: bool | None = None
