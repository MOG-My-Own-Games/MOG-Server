from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from config import BACKUP_HOUR, BACKUP_KEEP_DAILY, BACKUP_KEEP_MANUAL, BACKUP_KEEP_PRE_UPDATE, BACKUP_PATH
from handler import backups
from handler.auth import AdminUser
from handler.database import db_settings_handler

router = APIRouter(prefix="/backups", tags=["backups"])


class BackupSchema(BaseModel):
    name: str
    kind: str
    created_at: datetime
    size_bytes: int
    includes_saves: bool


class BackupsSchema(BaseModel):
    available: bool
    # Why there are no backups, when there are none to be made (no volume mounted).
    reason: str | None
    path: str
    own_volume: bool
    free_bytes: int | None
    daily: bool
    daily_saves: bool
    hour: int
    keep: dict[str, int]
    last_error: str | None
    backups: list[BackupSchema]


@router.get("")
async def get_backups(user: AdminUser) -> BackupsSchema:
    settings = db_settings_handler.get_settings()
    why = backups.available()
    return BackupsSchema(
        available=why is None,
        reason=why,
        path=BACKUP_PATH,
        own_volume=backups.is_own_volume(),
        free_bytes=backups.free_bytes(),
        daily=backups.daily_enabled(settings),
        daily_saves=backups.saves_in_daily(settings),
        hour=BACKUP_HOUR,
        keep={backups.DAILY: BACKUP_KEEP_DAILY, backups.PRE_UPDATE: BACKUP_KEEP_PRE_UPDATE, backups.MANUAL: BACKUP_KEEP_MANUAL},
        last_error=backups.last_error,
        backups=[BackupSchema(**b) for b in backups.list_backups()],
    )


@router.post("")
async def make_backup(user: AdminUser, saves: bool = False) -> BackupSchema:
    """A manual backup now: the database, the avatars and uploaded artwork, and with `saves` the saves too (which can be large)."""
    try:
        made = await run_in_threadpool(backups.create, backups.MANUAL, saves)
    except backups.BackupUnavailable as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except backups.BackupBusy as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return BackupSchema(**made)


@router.get("/{name}/download")
async def download_backup(user: AdminUser, name: Annotated[str, Path(max_length=100)]) -> FileResponse:
    path = backups.path_of(name)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path, filename=name, media_type="application/gzip")


@router.delete("/{name}")
async def delete_backup(user: AdminUser, name: Annotated[str, Path(max_length=100)]) -> None:
    if not backups.delete(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
