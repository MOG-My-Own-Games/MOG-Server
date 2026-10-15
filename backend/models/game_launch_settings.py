"""How a game is launched, kept on the server so every device of a game starts it the same way: the Proton build, the
Wine settings winecfg changed, the winetricks entries kept for its prefix and the arguments (as tags) the executable starts with. One row per game and device; a row with no
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
# A winetricks entry is a verb (vcrun2019, dotnet48, d3dx9_43) or a setting with a value (renderer=gdi, vd=1280x1024). The clients
# put them on a command line, so only what they are made of: no space, quote, `$`, `;`, `|`, `&`, `/`, `\` or a second `=`, and an
# entry never starts like an option.
WINETRICKS_NAME = r"[a-z0-9][a-z0-9_.-]{0,39}"
WINETRICKS_VALUE = r"[A-Za-z0-9_.,:+-]{1,40}"
WINETRICKS_VERB = re.compile(WINETRICKS_NAME + "(?:=" + WINETRICKS_VALUE + ")?")
WINETRICKS_MAX_VERBS = 100


def winetricks_key(entry: str) -> str:
    """What an entry is about: the verb, or the setting's name (`vd` for `vd=1280x1024`)."""
    return entry.split("=", 1)[0]


def normalize_winetricks(entries: list[str]) -> list[str]:
    """One entry for each key: a later one replaces an earlier one with the same key (`vd=1920x1080` after `vd=1280x1024`),
    in the place the key first had."""
    kept: dict[str, str] = {}
    for entry in entries:
        kept[winetricks_key(entry)] = entry
    return list(kept.values())


# The arguments put after the executable at launch are a list of tags (see utils/launch_args.py): up to LAUNCH_MAX_TAGS of at most
# LAUNCH_TAG_MAX_LENGTH characters, each of which a client splits like a shell does.


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
    # Put after the game's executable when it starts, as tags (["-savetouserdir", "-res 1280 720"]); None: none were set.
    launch_args: Mapped[list[Any] | None] = mapped_column(CustomJSON(), default=None)
