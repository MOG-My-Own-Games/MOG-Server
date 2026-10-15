from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path as PathVar, UploadFile, status
from starlette.concurrency import run_in_threadpool

from config import RESOURCES_BASE_PATH
from endpoints.responses.user import PasswordChangeForm, UserCreateForm, UserSchema, UserUpdateForm
from handler.auth import AdminUser, CurrentUser, hash_password, verify_password
from handler.database import db_saves_handler, db_user_handler
from handler import media_uploads
from handler.saves import purge_user
from models.user import Role
from utils.image_cache import _kind_of

router = APIRouter(prefix="/users", tags=["users"])

_AVATAR_DIR = Path(RESOURCES_BASE_PATH) / "avatars"


@router.get("/me")
async def get_me(user: CurrentUser) -> UserSchema:
    return UserSchema.model_validate(user)


@router.post("/me/password")
async def change_my_password(user: CurrentUser, data: PasswordChangeForm) -> None:
    """A user changes their own password by proving the current one. The check
    fails with 400, not 401: a 401 makes the web UI sign the user out."""
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if data.new_password == data.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The new password must differ from the current one")
    db_user_handler.update_user(user.id, {"hashed_password": hash_password(data.new_password)})


@router.get("")
async def list_users(user: AdminUser) -> list[UserSchema]:
    return [UserSchema.model_validate(u) for u in db_user_handler.get_all_users()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(user: AdminUser, data: UserCreateForm) -> UserSchema:
    from models.user import User

    if db_user_handler.get_user_by_username(data.username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
    created = db_user_handler.add_user(
        User(username=data.username, hashed_password=hash_password(data.password), role=data.role)
    )
    return UserSchema.model_validate(created)


def _stops_being_an_active_admin(changes: dict) -> bool:
    return changes.get("enabled") is False or ("role" in changes and changes["role"] != Role.ADMIN)


@router.put("/{id}")
async def update_user(user: AdminUser, id: Annotated[int, PathVar(ge=1)], data: UserUpdateForm) -> UserSchema:
    update_data = data.model_dump(exclude_unset=True, exclude={"password"})
    target = db_user_handler.get_user(id)
    if target is not None and target.is_admin and target.enabled and _stops_being_an_active_admin(update_data):
        others = [u for u in db_user_handler.get_all_users() if u.id != id and u.is_admin and u.enabled]
        if not others:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="There must stay one enabled admin")
    if data.password:
        update_data["hashed_password"] = hash_password(data.password)
    updated = db_user_handler.update_user(id, update_data)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return UserSchema.model_validate(updated)


@router.delete("/{id}")
async def delete_user(user: AdminUser, id: Annotated[int, PathVar(ge=1)], delete_saves: bool = False) -> None:
    """Delete a user and, with them, their devices and saved game files. A user who has saves is
    refused with 409 `has_saves` (and what would be lost) unless `delete_saves` says to go on."""
    if id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete your own account")
    versions, size_bytes = db_saves_handler.summary(user_id=id)
    if versions and not delete_saves:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "has_saves", "versions": versions, "size_bytes": size_bytes},
        )
    await run_in_threadpool(purge_user, id)
    db_user_handler.delete_user(id)


@router.post("/{id}/avatar")
async def upload_avatar(user: CurrentUser, id: Annotated[int, PathVar(ge=1)], file: UploadFile) -> UserSchema:
    """A user can set their own avatar; an admin can set anyone's."""
    if id != user.id and not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    # What the file really is decides, not what the client says it is; the size is bounded before it is read whole.
    data = await file.read(media_uploads.MAX_UPLOAD_BYTES + 1)
    try:
        checked = await run_in_threadpool(media_uploads.check, data)
    except media_uploads.UploadRefused as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Must be a PNG, JPEG or WebP image: {e}") from e

    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    dest = _AVATAR_DIR / f"{id}{_kind_of(checked)[0]}"
    for old in _AVATAR_DIR.glob(f"{id}.*"):  # a picture of another type from before goes
        if old != dest:
            old.unlink(missing_ok=True)
    dest.write_bytes(checked)

    updated = db_user_handler.update_user(id, {"avatar_path": f"/resources/avatars/{dest.name}"})
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return UserSchema.model_validate(updated)
