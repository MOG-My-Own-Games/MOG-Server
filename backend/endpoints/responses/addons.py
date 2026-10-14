from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GameAddonSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    category: str
    name: str
    size_bytes: int
    file_count: int
    user_id: int | None = None
    session_id: int | None = None
    created_at: datetime


class GameAddonRenameForm(BaseModel):
    name: str
