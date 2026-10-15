"""Launch arguments as tags: a list in which each tag is one option with what belongs to it (`-res 1280 720`, `--nosound`), so
two editors (the web page, a client) can be merged by tag instead of one replacing the other's text."""

from __future__ import annotations

import re
import shlex

LAUNCH_TAG_MAX_LENGTH = 200
LAUNCH_MAX_TAGS = 50

_NEGATIVE_NUMBER = re.compile(r"-\d+(?:\.\d+)?")


def split_tags(text: str) -> list[str]:
    """Typed arguments as tags: a new tag starts at each word that begins with `-` or `--` (a negative number is a value, not
    an option), and the words after it, up to the next such word, are part of it. Quoting is kept (`-title 'My Game'`)."""
    try:
        words = shlex.split(text)
        join = shlex.join
    except ValueError:  # a quote left open: the words as they are, joined as they were
        words = text.split()
        join = " ".join
    tags: list[str] = []
    group: list[str] = []
    for word in words:
        if word.startswith("-") and not _NEGATIVE_NUMBER.fullmatch(word) and group:
            tags.append(join(group))
            group = []
        group.append(word)
    if group:
        tags.append(join(group))
    return list(dict.fromkeys(tags))


def is_tag(tag: object) -> bool:
    return (
        isinstance(tag, str)
        and tag == tag.strip()
        and 0 < len(tag) <= LAUNCH_TAG_MAX_LENGTH
        and not any(ord(c) < 32 or ord(c) == 127 for c in tag)
    )


def patch_tags(current: list[str] | None, add: list[str] | None, remove: list[str] | None) -> list[str]:
    """`current` without the tags in `remove`, then with those of `add` it lacks at the end: a change made on top of whatever
    the list is by now, so it does not undo what somebody else changed meanwhile."""
    gone = set(remove or [])
    kept = [t for t in (current or []) if t not in gone]
    return kept + [t for t in dict.fromkeys(add or []) if t not in kept]
