"""What a game's saves are, told by hand: a path that must never be backed up (a shader cache a port writes next to its saves) or
one that must be. Kept per game, not per user or device, so every client that syncs the game follows the same rules. A path is an
archive key, a file or a folder (everything below it), the same names the clients put in a save archive; or a pattern (`*.png`,
`game/*/shaders`, `re:\\.(png|jpg)$`) for the paths it fits."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import PurePosixPath

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

BLACKLIST = "blacklist"
WHITELIST = "whitelist"
KINDS = (BLACKLIST, WHITELIST)
PATH_MAX_LENGTH = 1000
REGEX_PREFIX = "re:"
REGEX_MAX_LENGTH = 200


def is_pattern(rule: str) -> bool:
    """A rule that is a pattern, not a path: `re:` and a regular expression, or a glob (`*`, `?` or a `[...]` class). A folder
    whose name has brackets is named with `?` in their place."""
    return rule.startswith(REGEX_PREFIX) or "*" in rule or "?" in rule or ("[" in rule and "]" in rule)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as a regular expression: `*` and `?` stay inside a folder name, `**` crosses folders, `[abc]` is one of them
    (`[!abc]` none of them). Letter case does not count: the files of a Wine prefix do not tell it either."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[" and (close := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : close].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body[0] in "!^" else body) + "]")
            i = close + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out), re.IGNORECASE)


@lru_cache(maxsize=256)
def _regex(rule: str) -> re.Pattern[str]:
    return re.compile(rule[len(REGEX_PREFIX) :])


def covers(rule: str, path: str) -> bool:
    """Whether a rule names `path`. A plain rule is the path itself or a folder above it. A glob without a `/` is matched against
    each name in the path (`*.png` is every png, wherever it is; `cache*` a folder and all that is in it), one with a `/` against
    the path from its start and each folder above it (`game/*/shaders`). `re:` is a regular expression searched in the path."""
    if rule.startswith(REGEX_PREFIX):
        return _regex(rule).search(path) is not None
    if not is_pattern(rule):
        return path == rule or path.startswith(rule + "/")
    parts = path.split("/")
    rx = _glob_regex(rule)
    if "/" not in rule:
        return any(rx.fullmatch(part) for part in parts)
    return any(rx.fullmatch("/".join(parts[:n])) for n in range(1, len(parts) + 1))


def is_left_out(path: str, blacklist: list[str], whitelist: list[str]) -> bool:
    """Whether the blacklist leaves `path` out. The whitelist is stronger: a path it names (itself or a folder above it) is kept
    whatever the blacklist says, so `game/thisgame` blacklisted and `game/thisgame/saves` added leaves out all of the game but
    its saves."""
    return any(covers(r, path) for r in blacklist) and not any(covers(r, path) for r in whitelist)


def clean_path(raw: str) -> str:
    """The rule `raw` stands for, or ValueError. A path is an archive key: relative, no `..`, no backslash, drive letter or empty
    part. A glob follows the same rules; a `re:` rule must compile and is kept as it is."""
    text = raw.strip()
    if text.startswith(REGEX_PREFIX):
        pattern = text[len(REGEX_PREFIX) :]
        if not pattern or len(pattern) > REGEX_MAX_LENGTH:
            raise ValueError(f"a regular expression takes 1 to {REGEX_MAX_LENGTH} characters: {raw!r}")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"not a regular expression ({e}): {raw!r}") from e
        return text
    path = text.rstrip("/")
    parts = path.split("/")
    if not path or path.startswith("/") or len(path) > PATH_MAX_LENGTH or "\\" in path or "\0" in path or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"not a save path: {raw!r}")
    if ":" in parts[0]:
        raise ValueError(f"not a save path: {raw!r}")
    if is_pattern(path):
        try:
            _glob_regex(path)
        except re.error as e:
            raise ValueError(f"not a pattern ({e}): {raw!r}") from e
        return path
    return str(PurePosixPath(path))


class GameSaveRule(BaseModel):
    __tablename__ = "game_save_rules"
    __table_args__ = (Index("ix_game_save_rules_game_path", "game_id", "path", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(length=PATH_MAX_LENGTH))
    kind: Mapped[str] = mapped_column(String(length=16))

    def __repr__(self) -> str:
        return f"GameSaveRule(game={self.game_id} {self.kind} {self.path})"
