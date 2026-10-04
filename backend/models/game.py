"""A Game is one top-level entry (file or directory) under a Library's root_path.
Replaces RomM's models/rom.py: no platform, no per-provider hash matching, no
retro-emulation fields - just enough to identify it on disk and show it in a
library, plus IGDB/SteamGridDB metadata (see handler/metadata)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel
from utils.database import CustomJSON

if TYPE_CHECKING:
    from models.install_session import InstallSession
    from models.library import Library

NAME_MAX_LENGTH = 400
FS_NAME_MAX_LENGTH = 450


class Game(BaseModel):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))

    # Name of the file or directory directly under the library's root_path.
    fs_name: Mapped[str] = mapped_column(String(length=FS_NAME_MAX_LENGTH))

    # Display name: defaults to fs_name, overridable once metadata is matched.
    name: Mapped[str] = mapped_column(String(length=NAME_MAX_LENGTH))
    summary: Mapped[str | None] = mapped_column(Text(), default=None)

    igdb_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    igdb_metadata: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)
    sgdb_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    cover_path: Mapped[str | None] = mapped_column(String(length=1000), default=None)
    # The artwork chosen for each kind (see handler/media.py): {kind: {"url": ..., "source": ...}}.
    # cover_path mirrors media["cover"] for what reads it directly.
    media: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)

    # Set by a scan when the file/directory is gone from disk; cleared if it reappears.
    missing_from_fs: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")

    # Plain lazy loading for Phase 1 simplicity (RomM uses lazy="raise" + explicit
    # eager-loading everywhere to catch N+1s; worth adopting once this grows).
    library: Mapped["Library"] = relationship(lazy="joined", back_populates="games")
    install_sessions: Mapped[list["InstallSession"]] = relationship(
        lazy="select", back_populates="game", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Game({self.id} {self.name!r})"
