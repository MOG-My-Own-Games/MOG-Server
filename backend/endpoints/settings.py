from __future__ import annotations

from fastapi import APIRouter

from endpoints.responses.settings import SettingsSchema, SettingsUpdateForm
from handler.auth import AdminUser
from handler.database import db_settings_handler

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
