from __future__ import annotations

from pydantic import BaseModel


class SettingsSchema(BaseModel):
    igdb_client_id: str | None
    igdb_client_secret: str | None
    steamgriddb_api_key: str | None
    install_cache_ttl_days: int | None
    install_default_proton_build: str | None


class SettingsUpdateForm(BaseModel):
    igdb_client_id: str | None = None
    igdb_client_secret: str | None = None
    steamgriddb_api_key: str | None = None
    install_cache_ttl_days: int | None = None
    install_default_proton_build: str | None = None
