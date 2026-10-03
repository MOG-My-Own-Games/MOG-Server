from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    body: str | None
    game_id: int | None
    session_id: int | None
    read: bool
    created_at: datetime


class NotificationsSchema(BaseModel):
    notifications: list[NotificationSchema]
    unread: int
