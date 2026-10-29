"""One run of a game, from when it started to when it closed, by one user. The time of a game is the sum of its sessions.

Sessions rather than a running total so that a client that sends one twice (a reply that got lost, then a retry from its outbox)
counts it once: a session is told by the moment it started."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

# One session is never longer than this: a game left open for days is more likely a machine that slept than a person playing.
SESSION_MAX_SECONDS = 24 * 60 * 60


class GamePlaySession(BaseModel):
    __tablename__ = "game_play_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "game_id", "started_at", name="uq_game_play_sessions_start"),
        Index("ix_game_play_sessions_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    # The machine it was played on; kept when only the device is forgotten.
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"), default=None)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    seconds: Mapped[int] = mapped_column(Integer)
