from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from starlette.concurrency import run_in_threadpool

from endpoints.responses.save import DeviceSchema, DeviceUpdateForm, RegisterDeviceForm
from handler.auth import CurrentUser
from handler.database import db_device_handler
from handler.devices import DeviceNotFound, HostnameTaken, NameTaken, register_device, rename_device

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices(user: CurrentUser) -> list[DeviceSchema]:
    return [DeviceSchema.model_validate(d) for d in db_device_handler.get_for_user(user.id)]


@router.post("/register")
async def register(user: CurrentUser, form: RegisterDeviceForm) -> DeviceSchema:
    """Find or create this client's device. A 409 `hostname_taken` lists the devices that already
    use the hostname and some free names, so the client can ask whether it is one of them."""
    try:
        device = await run_in_threadpool(
            register_device,
            user.id,
            form.client_uid,
            form.hostname,
            form.platform,
            form.os_id,
            form.adopt_device_id,
            form.name,
        )
    except HostnameTaken as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "hostname_taken",
                "devices": [DeviceSchema.model_validate(d).model_dump(mode="json") for d in e.devices],
                "suggested_names": e.suggested_names,
            },
        ) from e
    except NameTaken as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "name_taken"}) from e
    except DeviceNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from e
    return DeviceSchema.model_validate(device)


@router.patch("/{id}")
async def update_device(
    user: CurrentUser, id: Annotated[int, Path(ge=1)], form: DeviceUpdateForm
) -> DeviceSchema:
    try:
        device = await run_in_threadpool(rename_device, user.id, id, form.name)
    except NameTaken as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "name_taken"}) from e
    except DeviceNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from e
    return DeviceSchema.model_validate(device)
