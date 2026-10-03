from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from endpoints.responses.notification import NotificationSchema, NotificationsSchema
from handler.auth import CurrentUser
from handler.database import db_notification_handler

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(user: CurrentUser) -> NotificationsSchema:
    return NotificationsSchema(
        notifications=[NotificationSchema.model_validate(n) for n in db_notification_handler.get_for_user(user.id)],
        unread=db_notification_handler.count_unread(user.id),
    )


@router.post("/read")
async def mark_all_read(user: CurrentUser) -> None:
    db_notification_handler.mark_read(user.id)


@router.post("/{id}/read")
async def mark_read(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> None:
    db_notification_handler.mark_read(user.id, id)


@router.delete("")
async def clear_notifications(user: CurrentUser) -> dict:
    return {"cleared": db_notification_handler.delete(user.id)}


@router.delete("/{id}")
async def delete_notification(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> None:
    db_notification_handler.delete(user.id, id)
