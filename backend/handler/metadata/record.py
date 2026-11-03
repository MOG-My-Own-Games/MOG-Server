"""The metadata one provider has for one game, in the one shape MOG stores (Game.igdb_metadata) and every client reads."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def make(
    id: int | str,
    name: str,
    *,
    url: str | None = None,
    summary: str | None = None,
    cover: str | None = None,
    screenshots: Iterable[str] = (),
    genres: Iterable[str] = (),
    developers: Iterable[str] = (),
    aliases: Iterable[str] = (),
    first_release_date: int | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": name,
        "url": url,
        "summary": summary,
        "cover": {"url": cover} if cover else None,
        "screenshots": [{"url": shot} for shot in screenshots if shot],
        "genres": [{"name": genre} for genre in genres if genre],
        "developers": [{"name": dev} for dev in developers if dev],
        # Other names the game goes by (a translated or original title): what a name is matched against besides `name`.
        "aliases": [alias for alias in dict.fromkeys(aliases) if alias and alias != name],
        "first_release_date": first_release_date,
    }
