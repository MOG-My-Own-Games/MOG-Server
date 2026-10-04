"""The artwork a game uses, kind by kind: cover, banner, hero, logo (its title) and icon.

SteamGridDB is the primary source for all of them, IGDB the secondary one for the kinds it has
(its cover, and its artworks and screenshots as hero art). A scrape picks the first candidate of
each kind on its own; the scrape dialog lets a person pick differently. The chosen images are
cached and served by this server, so clients (and the Steam shortcuts they build) never depend on
the providers' CDNs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from handler.metadata import sgdb_handler
from models.game import Game

KINDS = ("cover", "banner", "hero", "logo", "icon")
SGDB, IGDB = "steamgriddb", "igdb"
_HOST_SOURCES = {"steamgriddb.com": SGDB, "images.igdb.com": IGDB}


def source_of(url: str) -> str | None:
    """Which provider a URL belongs to, or None for a host that is not allowed."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        return None
    for suffix, source in _HOST_SOURCES.items():
        if host == suffix or host.endswith("." + suffix):
            return source
    return None


def _candidate(url: str, source: str, thumb: str | None = None, width: int | None = None, height: int | None = None) -> dict:
    return {"url": url, "thumb": thumb or url, "source": source, "width": width, "height": height}


def igdb_candidates(metadata: dict[str, Any] | None) -> dict[str, list[dict]]:
    meta = metadata or {}
    found: dict[str, list[dict]] = {}
    cover = (meta.get("cover") or {}).get("url")
    if cover:
        found["cover"] = [_candidate(cover, IGDB)]
    wide = [a["url"] for a in meta.get("artworks") or [] if a.get("url")]
    wide += [s["url"] for s in meta.get("screenshots") or [] if s.get("url")]
    if wide:
        found["hero"] = [_candidate(url, IGDB) for url in wide]
    return found


def candidates(game: Game, sgdb_id: int | None = None) -> dict[str, list[dict]]:
    """Every artwork on offer for the game, by kind, SteamGridDB's first."""
    found: dict[str, list[dict]] = {kind: [] for kind in KINDS}
    sgdb = sgdb_handler.get_media(sgdb_id or game.sgdb_id) if (sgdb_id or game.sgdb_id) else {}
    for kind, items in (sgdb or {}).items():
        found[kind] += [_candidate(i["url"], SGDB, i.get("thumb"), i.get("width"), i.get("height")) for i in items]
    for kind, items in igdb_candidates(game.igdb_metadata).items():
        found[kind] += items
    for kind in KINDS:  # the same image from two places counts once
        seen: set[str] = set()
        found[kind] = [c for c in found[kind] if not (c["url"] in seen or seen.add(c["url"]))]
    return found


def defaults(found: dict[str, list[dict]]) -> dict[str, dict]:
    """The provider's own pick for each kind: the first candidate."""
    return {kind: {"url": items[0]["url"], "source": items[0]["source"]} for kind, items in found.items() if items}


def selection(url: str | None) -> dict | None:
    source = source_of(url) if url else None
    return {"url": url, "source": source} if url and source else None
