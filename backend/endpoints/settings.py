from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from endpoints.responses.settings import SettingsSchema, SettingsUpdateForm, SettingsValidationSchema
from handler.auth import AdminUser
from handler.database import db_settings_handler
from handler.metadata import igdb_handler, sgdb_handler

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(user: AdminUser) -> SettingsSchema:
    return SettingsSchema.model_validate(db_settings_handler.get_settings(), from_attributes=True)


@router.put("")
async def update_settings(user: AdminUser, data: SettingsUpdateForm) -> SettingsSchema:
    """Partial update: only fields actually present in the request body are
    touched (exclude_unset) - every field here is independently Optional, so
    without this a client saving just one (e.g. the cache TTL) would send
    the rest as their Pydantic default (None) and silently wipe them."""
    row = db_settings_handler.update_settings(data.model_dump(exclude_unset=True))
    return SettingsSchema.model_validate(row, from_attributes=True)


@router.get("/validate")
async def validate_settings(user: AdminUser) -> SettingsValidationSchema:
    """Live check of the configured provider keys - a green/red indicator
    next to each field in Settings, not a gate on anything (a request with a
    bad key already just fails closed with an empty result on its own)."""
    settings = db_settings_handler.get_settings()
    igdb_configured = bool(settings.igdb_client_id and settings.igdb_client_secret)
    sgdb_configured = bool(settings.steamgriddb_api_key)
    return SettingsValidationSchema(
        igdb_valid=await run_in_threadpool(igdb_handler.validate_credentials) if igdb_configured else None,
        steamgriddb_valid=await run_in_threadpool(sgdb_handler.validate_key) if sgdb_configured else None,
    )
