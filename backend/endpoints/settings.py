from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from endpoints.responses.settings import SettingsSchema, SettingsUpdateForm, SettingsValidationSchema
from handler import library_watcher
from handler.auth import AdminUser
from handler.database import db_settings_handler
from handler.metadata import hltb_handler, igdb_handler, sgdb_handler
from models.settings import Settings

router = APIRouter(prefix="/settings", tags=["settings"])


def _schema(row: Settings) -> SettingsSchema:
    fields = {name: getattr(row, name) for name in SettingsSchema.model_fields}
    fields["igdb_enabled"] = igdb_handler.enabled_in(row)
    fields["steamgriddb_enabled"] = sgdb_handler.enabled_in(row)
    fields["hltb_enabled"] = hltb_handler.enabled_in(row)
    fields["watch_libraries"] = library_watcher.enabled_in(row)
    return SettingsSchema(**fields)


@router.get("")
async def get_settings(user: AdminUser) -> SettingsSchema:
    return _schema(db_settings_handler.get_settings())


@router.put("")
async def update_settings(user: AdminUser, data: SettingsUpdateForm) -> SettingsSchema:
    """Partial update: only fields actually present in the request body are
    touched (exclude_unset) - every field here is independently Optional, so
    without this a client saving just one (e.g. the cache TTL) would send
    the rest as their Pydantic default (None) and silently wipe them."""
    row = db_settings_handler.update_settings(data.model_dump(exclude_unset=True))
    return _schema(row)


@router.get("/validate")
async def validate_settings(
    user: AdminUser, provider: Literal["igdb", "steamgriddb"] | None = None
) -> SettingsValidationSchema:
    """Live check of the configured provider keys - a green/red indicator
    next to each field in Settings, not a gate on anything (a request with a
    bad key already just fails closed with an empty result on its own).

    With `provider` only that one is asked, so saving one field does not call the other provider. A provider
    with a key missing (IGDB needs both its values) or switched off is not called at all."""
    settings = db_settings_handler.get_settings()
    # A provider switched off is not checked: its credentials stay saved but nothing uses them.
    igdb_configured = (
        provider in (None, "igdb")
        and bool(settings.igdb_client_id and settings.igdb_client_secret)
        and igdb_handler.enabled_in(settings)
    )
    sgdb_configured = (
        provider in (None, "steamgriddb") and bool(settings.steamgriddb_api_key) and sgdb_handler.enabled_in(settings)
    )
    return SettingsValidationSchema(
        igdb_valid=await run_in_threadpool(igdb_handler.validate_credentials) if igdb_configured else None,
        steamgriddb_valid=await run_in_threadpool(sgdb_handler.validate_key) if sgdb_configured else None,
    )
