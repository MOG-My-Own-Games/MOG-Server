from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from config import SAVES_KEEP_VERSIONS
from endpoints.responses.save import (
    DeviceSavesSchema,
    DeviceSchema,
    GameSavesSchema,
    SaveUploadSchema,
    SaveVersionDetailSchema,
    SaveVersionSchema,
)
from handler.auth import CurrentUser
from handler.database import db_device_handler, db_game_handler, db_saves_handler
from handler.notifications import notify_save_restored, notify_save_synced
from handler.saves import UploadTooLarge, receive_upload, remove_version, resolve_path, store_version
from models.device import Device
from models.game import Game
from models.save_version import SaveVersion
from models.user import User
from utils.archive_safety import ArchiveError

router = APIRouter(tags=["saves"])

Trigger = Literal["launch", "quit", "manual", "uninstall", "sync"]


def _visible_game(user: User, game_id: int) -> Game:
    game = db_game_handler.get_game(game_id)
    if game is None or (not user.is_admin and game.library_id in user.hidden_library_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return game


def _own_device(user: User, device_id: int) -> Device:
    device = db_device_handler.get_device(device_id)
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown device")
    return device


def _own_version(user: User, version_id: int) -> SaveVersion:
    version = db_saves_handler.get_version(version_id)
    if version is None or version.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return version


@router.get("/games/{id}/saves")
async def list_game_saves(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> GameSavesSchema:
    _visible_game(user, id)
    versions = db_saves_handler.get_for_game(user.id, id)
    devices = {d.id: d for d in db_device_handler.get_for_user(user.id)}
    grouped: dict[int, list[SaveVersion]] = {}
    for version in versions:  # newest first, so each device's list and the device order are by recency
        if version.device_id in devices:
            grouped.setdefault(version.device_id, []).append(version)
    return GameSavesSchema(
        devices=[
            DeviceSavesSchema(
                device=DeviceSchema.model_validate(devices[device_id]),
                versions=[SaveVersionSchema.model_validate(v) for v in rows],
            )
            for device_id, rows in grouped.items()
        ],
        keep_versions=SAVES_KEEP_VERSIONS,
    )


@router.post("/games/{id}/saves")
async def upload_game_saves(
    user: CurrentUser,
    id: Annotated[int, Path(ge=1)],
    device_id: Annotated[int, Query(ge=1)],
    file: UploadFile,
    trigger: Trigger = "manual",
) -> SaveUploadSchema:
    """Store a zip of a game's save files as the newest version of one of the user's devices."""
    _visible_game(user, id)
    device = _own_device(user, device_id)
    try:
        incoming = await receive_upload(file)
    except UploadTooLarge as e:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Save archive too large"
        ) from e
    try:
        version, created = await run_in_threadpool(store_version, user.id, id, device_id, trigger, incoming)
    except ArchiveError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    if created:
        await run_in_threadpool(notify_save_synced, user.id, id, device.name, trigger, version.file_count)
    return SaveUploadSchema(version=SaveVersionSchema.model_validate(version), created=created)


@router.get("/saves/{id}")
async def get_save(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> SaveVersionDetailSchema:
    return SaveVersionDetailSchema.model_validate(_own_version(user, id))


@router.get("/saves/{id}/download")
async def download_save(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> FileResponse:
    version = _own_version(user, id)
    try:
        path = resolve_path(version.file_path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from e
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The archive is gone from disk")
    game = db_game_handler.get_game(version.game_id)
    device = db_device_handler.get_device(version.device_id)
    label = f"{game.name if game else version.game_id} - {device.name if device else version.device_id}"
    stamp = version.created_at.strftime("%Y-%m-%d_%H-%M-%S")
    filename = re.sub(r'[\\/:*?"<>|\r\n]+', "_", f"{label} - {stamp}.zip")
    return FileResponse(path, filename=filename, media_type="application/zip")


@router.post("/saves/{id}/restored")
async def save_restored(
    user: CurrentUser,
    id: Annotated[int, Path(ge=1)],
    device_id: Annotated[int, Query(ge=1)],
    files: Annotated[int, Query(ge=0)] = 0,
) -> None:
    """A client says it has put this version back on one of the user's devices: the user finds that in their
    notifications, next to the one that told them it was backed up."""
    version = _own_version(user, id)
    device = _own_device(user, device_id)
    source = db_device_handler.get_device(version.device_id)
    await run_in_threadpool(
        notify_save_restored, user.id, version.game_id, device.name, source.name if source else None, files
    )


@router.delete("/saves/{id}")
async def delete_save(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> None:
    await run_in_threadpool(remove_version, _own_version(user, id))
