"""A Library is one scanned folder of PC installers. MOG has no platform/console
concept (see RomM's models/platform.py, which this replaces): every library is
just a folder, scanned the same way regardless of what's in it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel

if TYPE_CHECKING:
    from models.game import Game

NAME_MAX_LENGTH = 255
PATH_MAX_LENGTH = 1000


class Library(BaseModel):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(length=NAME_MAX_LENGTH))
    # Absolute path on disk (inside the container), scanned non-recursively
    # into one Game per top-level entry.
    root_path: Mapped[str] = mapped_column(String(length=PATH_MAX_LENGTH), unique=True)

    games: Mapped[list["Game"]] = relationship(
        lazy="select", back_populates="library", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Library({self.id} {self.name!r} -> {self.root_path})"
