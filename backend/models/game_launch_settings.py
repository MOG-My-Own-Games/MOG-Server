"""How a game is launched, kept on the server so every device of a game starts it the same way: the Proton build, the
Wine settings winecfg changed and the winetricks verbs installed in its prefix. One row per game and device; a row with no
device holds the settings for all of them."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel
from utils.database import CustomJSON

PROTON_MAX_LENGTH = 200
# Registry export of what winecfg changed; larger than any real one by a wide margin.
WINECFG_REG_MAX_LENGTH = 64 * 1024
# A winetricks verb (vcrun2019, dotnet48, d3dx9_43): the clients put them on a command line, so only what verbs are made of.
WINETRICKS_VERB = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}")
WINETRICKS_MAX_VERBS = 100


class GameLaunchSettings(BaseModel):
    __tablename__ = "game_launch_settings"
    # SQLite treats every NULL as distinct, so the handler keeps the one shared row per game unique itself.
    __table_args__ = (Index("ix_game_launch_settings_game_device", "game_id", "device_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), default=None)
    # The build's name, not a path: paths differ between devices.
    proton: Mapped[str | None] = mapped_column(String(length=PROTON_MAX_LENGTH), default=None)
    winecfg_reg: Mapped[str | None] = mapped_column(Text(), default=None)
    # The verbs installed in the prefix, to put into a new one (a reinstall, another device).
    winetricks: Mapped[list[Any] | None] = mapped_column(CustomJSON(), default=None)
