"""A message for one user: something happened that they should know about
(an auto mode install that got stuck or failed, saves that were backed up, games that were added)."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

TITLE_MAX_LENGTH = 200
BODY_MAX_LENGTH = 1000

KIND_AUTO_MODE_STUCK = "auto_mode_stuck"
KIND_AUTO_MODE_FAILED = "auto_mode_failed"
KIND_SAVE_SYNCED = "save_synced"
KIND_GAMES_ADDED = "games_added"
KIND_MOD_READY = "mod_ready"
KIND_MOD_FAILED = "mod_failed"


class Notification(BaseModel):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read", "user_id", "read"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(length=32))
    title: Mapped[str] = mapped_column(String(length=TITLE_MAX_LENGTH))
    body: Mapped[str | None] = mapped_column(String(length=BODY_MAX_LENGTH), default=None)
    # The game the notification is about; the row survives the game's removal.
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id", ondelete="SET NULL"), default=None)
    session_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    read: Mapped[bool] = mapped_column(Boolean(), default=False, server_default=sa_false(), nullable=False)
