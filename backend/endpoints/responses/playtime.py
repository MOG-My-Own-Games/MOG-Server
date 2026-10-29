from __future__ import annotations

from pydantic import BaseModel, Field

from endpoints.responses.save import UtcDatetime
from models.game_play_session import SESSION_MAX_SECONDS


class PlaytimeForm(BaseModel):
    """One run of a game: when it started and how long it was open, saves backup not counted."""

    started_at: UtcDatetime
    seconds: int = Field(ge=1, le=SESSION_MAX_SECONDS)
    device_id: int | None = Field(default=None, ge=1)


class PlaytimeSchema(BaseModel):
    game_id: int
    # What the user has played of the game, over every session and device.
    playtime_seconds: int
    sessions: int
    # False when this session was already known (a client sending it again): nothing was added.
    recorded: bool = True
