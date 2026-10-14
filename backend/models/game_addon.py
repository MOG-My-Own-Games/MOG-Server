"""A mod, patch, DLC or extra of a game kept on the server (see handler/addons.py). The content is one zip in a folder of its
own, under ADDONS_PATH; the row is what the game's Files and the install cache list."""

from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

# "extra" is any other addon that is not a mod, a patch or a DLC: a generic folder of files added on top of a game.
ADDON_CATEGORIES = ("mod", "patch", "dlc", "extra")
ADDON_NAME_MAX_LENGTH = 120


class GameAddon(BaseModel):
    __tablename__ = "game_addons"
    __table_args__ = (Index("ix_game_addons_game", "game_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(length=16))
    # The last file the install put in, as it is named in the installer order.
    name: Mapped[str] = mapped_column(String(length=ADDON_NAME_MAX_LENGTH))
    size_bytes: Mapped[int] = mapped_column(BigInteger(), default=0)
    file_count: Mapped[int] = mapped_column(Integer(), default=0)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    # The install that made it, when a chain made it (NULL for an upload).
    session_id: Mapped[int | None] = mapped_column(Integer(), default=None)
