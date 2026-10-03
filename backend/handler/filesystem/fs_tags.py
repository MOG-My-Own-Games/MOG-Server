"""Tags from a game's file/folder name: every `(...)` or `[...]` chunk, split on commas."""

from __future__ import annotations

import re

_TAG_REGEX = re.compile(r"\(([^)]+)\)|\[([^\]]+)\]")


def parse_fs_tags(fs_name: str) -> list[str]:
    tags: list[str] = []
    for match in _TAG_REGEX.finditer(fs_name):
        for chunk in (match[1] or match[2]).split(","):
            tag = chunk.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags
