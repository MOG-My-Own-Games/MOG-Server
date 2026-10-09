from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse

from config import MAX_DEVICE_LOG_BYTES

from endpoints.responses.save import DeviceSchema, DeviceUpdateForm, RegisterDeviceForm
from handler.auth import CurrentUser
from handler.database import db_device_handler
from handler import device_logs
from handler.devices import DeviceNotFound, HostnameTaken, NameTaken, register_device, rename_device

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("")
async def list_devices(user: CurrentUser) -> list[DeviceSchema]:
    return [
        DeviceSchema.model_validate(d).model_copy(update={"log_at": device_logs.uploaded_at(user.id, d.id)})
        for d in db_device_handler.get_for_user(user.id)
    ]


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


def _own_device(user_id: int, id: int):
    device = db_device_handler.get_device(id)
    if device is None or device.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return device


@router.put("/{id}/log", status_code=status.HTTP_204_NO_CONTENT)
async def put_device_log(user: CurrentUser, id: Annotated[int, Path(ge=1)], request: Request) -> None:
    """The client's log as plain text, replacing the one sent before. Only the newest part is kept."""
    _own_device(user.id, id)
    if int(request.headers.get("content-length") or 0) > 2 * MAX_DEVICE_LOG_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    await run_in_threadpool(device_logs.store, user.id, id, await request.body())


@router.get("/{id}/log")
async def get_device_log(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> PlainTextResponse:
    _own_device(user.id, id)
    text = await run_in_threadpool(device_logs.read, user.id, id)
    if text is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return PlainTextResponse(text)
