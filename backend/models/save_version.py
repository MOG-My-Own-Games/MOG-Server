"""One uploaded snapshot of a game's save files, from one device. A version is a whole zip,
so it restores on its own; only the newest few per device are kept."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel
from utils.database import CustomJSON

TRIGGERS = ("launch", "quit", "manual", "uninstall", "sync")
TRIGGER_MAX_LENGTH = 16
# Entries kept in `manifest`; file_count holds the real total.
MANIFEST_MAX_ENTRIES = 1000


class SaveVersion(BaseModel):
    __tablename__ = "save_versions"
    __table_args__ = (Index("ix_save_versions_game_device", "user_id", "game_id", "device_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(length=TRIGGER_MAX_LENGTH))
    # Relative to config.SAVES_BASE_PATH.
    file_path: Mapped[str] = mapped_column(String(length=1000))
    size_bytes: Mapped[int] = mapped_column(Integer())
    # Over the archive's members, not its bytes: the same files zipped twice hash alike.
    content_hash: Mapped[str] = mapped_column(String(length=64))
    file_count: Mapped[int] = mapped_column(Integer())
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(CustomJSON(), default=list)

    def __repr__(self) -> str:
        return f"SaveVersion({self.id} game={self.game_id} device={self.device_id})"
