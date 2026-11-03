"""Tags from a game's file/folder name: every `(...)` or `[...]` chunk, split on commas, and the name that is left without them
(what RomM calls the name without tags): "Ryona no Mori [Black Megane Laboratory] [English Translation]" is the game
"Ryona no Mori" with two tags."""

from __future__ import annotations

import re

_TAG_REGEX = re.compile(r"\(([^)]+)\)|\[([^\]]+)\]")
ARCHIVE_EXT = re.compile(
    r"\.(exe|iso|zip|rar|7z|tar|gz|sh|run|bin|msi|appimage|pkg|deb|dmg|cue|img|mdf|nrg|chd)$", re.IGNORECASE
)


def name_without_tags(fs_name: str) -> str:
    """The name a game shows until a provider names it: the file or folder name without its archive extension and its
    `(...)` / `[...]` chunks (those are the game's tags). A name that is only tags stays as it is."""
    name = ARCHIVE_EXT.sub("", fs_name)
    name = " ".join(_TAG_REGEX.sub(" ", name).split())
    return name or fs_name


def parse_fs_tags(fs_name: str) -> list[str]:
    tags: list[str] = []
    for match in _TAG_REGEX.finditer(fs_name):
        for chunk in (match[1] or match[2]).split(","):
            tag = chunk.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags
