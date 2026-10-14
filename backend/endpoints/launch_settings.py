from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status

from endpoints.responses.launch_settings import (
    DeviceLaunchSettingsSchema,
    GameLaunchSettingsSchema,
    LaunchSettingsForm,
    LaunchSettingsSchema,
)
from endpoints.responses.save import DeviceSchema
from endpoints.saves import _own_device, _visible_game
from handler.auth import CurrentUser
from handler.database import db_device_handler, db_launch_settings_handler
from logger.logger import log

router = APIRouter(tags=["launch-settings"])


def _schema(row) -> LaunchSettingsSchema | None:
    return LaunchSettingsSchema.model_validate(row) if row is not None else None


@router.get("/games/{id}/launch-settings")
async def get_launch_settings(
    user: CurrentUser,
    id: Annotated[int, Path(ge=1)],
    device_id: Annotated[int | None, Query(ge=1)] = None,
) -> GameLaunchSettingsSchema:
    _visible_game(user, id)
    if device_id is not None:
        _own_device(user, device_id)
    rows = db_launch_settings_handler.list_for_game(id)
    by_device = {r.device_id: r for r in rows}
    return GameLaunchSettingsSchema(
        shared=_schema(by_device.get(None)),
        device=_schema(by_device.get(device_id)) if device_id is not None else None,
        devices=[
            DeviceLaunchSettingsSchema(
                device=DeviceSchema.model_validate(d),
                proton=by_device[d.id].proton,
                winecfg_reg=by_device[d.id].winecfg_reg,
                winetricks=by_device[d.id].winetricks,
                launch_args=by_device[d.id].launch_args,
                updated_at=by_device[d.id].updated_at,
            )
            for d in db_device_handler.get_for_user(user.id)
            if d.id in by_device
        ],
    )


@router.put("/games/{id}/launch-settings")
async def put_launch_settings(
    user: CurrentUser, id: Annotated[int, Path(ge=1)], form: LaunchSettingsForm
) -> LaunchSettingsSchema:
    """Set the Proton build, the winecfg changes, the winetricks verbs and the launch arguments of a game, for one device or for all of them."""
    _visible_game(user, id)
    target = None
    if form.scope == "device":
        target = _own_device(user, form.device_id).id  # type: ignore[arg-type]
    changes = form.model_dump(exclude_unset=True, include={"proton", "winecfg_reg", "winetricks", "launch_args"})
    log.debug(f"Launch settings: game {id} {form.scope} {target}, changing {sorted(changes)} for user {user.id}")
    return LaunchSettingsSchema.model_validate(db_launch_settings_handler.save(id, target, changes))


@router.delete("/games/{id}/launch-settings")
async def delete_launch_settings(
    user: CurrentUser,
    id: Annotated[int, Path(ge=1)],
    scope: Literal["device", "all"],
    device_id: Annotated[int | None, Query(ge=1)] = None,
) -> None:
    _visible_game(user, id)
    target = None
    if scope == "device":
        if device_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="device_id is required for the device scope")
        target = _own_device(user, device_id).id
    db_launch_settings_handler.delete(id, target)
