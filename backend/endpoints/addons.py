"""Mods, patches, DLC and extras kept on the server (see handler/addons.py). Every user can list, download and add them; only an
admin, or the person who added one, can delete it."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path as PathVar, UploadFile, status
from starlette.responses import FileResponse

from config import ADDONS_MAX_BYTES, ADDONS_PATH
from endpoints.responses.addons import GameAddonRenameForm, GameAddonSchema
from handler import addons as addons_handler
from handler.auth import CurrentUser
from handler.database import db_game_addons_handler, db_game_handler
from handler.visibility import check_visible
from logger.logger import log

router = APIRouter(prefix="/games", tags=["addons"])
_CHUNK = 1024 * 1024


def _game_or_404(user, game_id: int):
    return check_visible(user, db_game_handler.get_game(game_id))


def _addon_of_game(game_id: int, addon_id: int):
    addon = db_game_addons_handler.get(addon_id)
    if addon is None or addon.game_id != game_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return addon


@router.get("/addons")
async def list_all_addons(user: CurrentUser) -> list[GameAddonSchema]:
    """Every entity of every game, for the install cache view."""
    rows = db_game_addons_handler.list_all()
    if not user.is_admin and user.hidden_library_ids:
        visible = {g.id for g in db_game_handler.get_all_games() if g.library_id not in user.hidden_library_ids}
        rows = [a for a in rows if a.game_id in visible]
    return [GameAddonSchema.model_validate(a) for a in rows]


@router.get("/{id}/addons")
async def list_addons(user: CurrentUser, id: Annotated[int, PathVar(ge=1)]) -> list[GameAddonSchema]:
    _game_or_404(user, id)
    return [GameAddonSchema.model_validate(a) for a in db_game_addons_handler.list_for_game(id)]


@router.post("/{id}/addons", status_code=status.HTTP_201_CREATED)
async def add_addon(
    user: CurrentUser,
    id: Annotated[int, PathVar(ge=1)],
    category: Annotated[str, Form()],
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> GameAddonSchema:
    """Keep a zip of files the user installed (the diff of a mod, patch, DLC or extra) as an entity of the game."""
    _game_or_404(user, id)
    try:
        addons_handler.check_category(category)
        addons_handler.sanitize_name(name)
    except addons_handler.AddonError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    incoming = Path(ADDONS_PATH) / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=incoming, suffix=".zip", delete=False)
    received = Path(handle.name)
    try:
        total = 0
        with handle:
            while chunk := await file.read(_CHUNK):
                total += len(chunk)
                if total > ADDONS_MAX_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="The upload is too large")
                handle.write(chunk)
        try:
            row = await asyncio.to_thread(addons_handler.add_zip, id, category, name, received, user.id)
        except addons_handler.AddonError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    finally:
        received.unlink(missing_ok=True)
    log.debug(f"Addon {row.id} ({category}) added to game {id} by user {user.id}")
    return GameAddonSchema.model_validate(row)


ADDON_FILES_LISTED = 500  # how many of an addon's files its listing names; the count is always the whole of them


@router.get("/{id}/addons/{addon_id}/files")
async def list_addon_files(
    user: CurrentUser,
    id: Annotated[int, PathVar(ge=1)],
    addon_id: Annotated[int, PathVar(ge=1)],
) -> dict:
    """What an addon puts in the game's folder: its file count and the first files of it, with their sizes."""
    _game_or_404(user, id)
    addon = _addon_of_game(id, addon_id)
    try:
        count, files = await asyncio.to_thread(addons_handler.list_files, addon, ADDON_FILES_LISTED)
    except addons_handler.AddonError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return {"file_count": count, "files": [{"path": p, "size": s} for p, s in files]}


@router.get("/{id}/addons/{addon_id}/download")
async def download_addon(
    user: CurrentUser,
    id: Annotated[int, PathVar(ge=1)],
    addon_id: Annotated[int, PathVar(ge=1)],
) -> FileResponse:
    _game_or_404(user, id)
    addon = _addon_of_game(id, addon_id)
    path = addons_handler.content_path(addon)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The entity's content is missing")
    return FileResponse(path, media_type="application/zip", filename=f"{addon.name}.zip")


@router.patch("/{id}/addons/{addon_id}")
async def rename_addon(
    user: CurrentUser,
    id: Annotated[int, PathVar(ge=1)],
    addon_id: Annotated[int, PathVar(ge=1)],
    form: GameAddonRenameForm,
) -> GameAddonSchema:
    """Give an entity another name. Its content, category and place on disk do not change."""
    _game_or_404(user, id)
    addon = _addon_of_game(id, addon_id)
    if not user.is_admin and addon.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or the person who added it can rename it")
    try:
        name = addons_handler.sanitize_name(form.name)
    except addons_handler.AddonError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    row = db_game_addons_handler.rename(addon_id, name)
    log.debug(f"Addon {addon_id} of game {id} renamed to {name!r} by user {user.id}")
    return GameAddonSchema.model_validate(row)


@router.delete("/{id}/addons/{addon_id}")
async def delete_addon(
    user: CurrentUser,
    id: Annotated[int, PathVar(ge=1)],
    addon_id: Annotated[int, PathVar(ge=1)],
) -> None:
    _game_or_404(user, id)
    addon = _addon_of_game(id, addon_id)
    if not user.is_admin and addon.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an admin or the person who added it can delete it")
    addons_handler.remove(addon)
    log.debug(f"Addon {addon_id} of game {id} deleted by user {user.id}")
