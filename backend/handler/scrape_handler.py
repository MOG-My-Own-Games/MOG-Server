"""Best-effort bulk metadata fill, in the spirit of RomM's scan+scrape (search
a provider by name, take the top result, apply it) but intentionally simpler:
no fuzzy-match scoring, no per-platform ranking, first result wins. Good
enough for a PC library where names are usually close to exact already;
revisit if that turns out not to hold (see docs/TODO.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from handler.database import db_game_handler
from handler.metadata import igdb_handler, sgdb_handler
from handler.name_matching import best_match, query_variants
from models.game import Game

_BRACKETED = re.compile(r"[\[(\{][^\])}]*[\])}]")
_VERSION = re.compile(r"[\s_.-]+v?\d+(\.\d+)+\b.*$", re.IGNORECASE)
_TAGS = re.compile(r"[\s_.-]+(gog|repack|setup|installer|multi\d*|goty|drm[\s_.-]?free)\b.*$", re.IGNORECASE)
_ARCHIVE_EXT = re.compile(r"\.(exe|iso|zip|rar|7z|tar|gz)$", re.IGNORECASE)


_SCENE_GROUP = re.compile(r"(?<=\S)-([A-Z0-9]{2,})$")


def search_name(raw: str) -> str:
    """Folder/file name reduced to a plausible game title for provider search:
    extension, bracketed tags, version and release suffixes dropped, dots and
    underscores read as spaces, and a trailing `-GROUP` of a scene-style name
    removed."""
    name = _ARCHIVE_EXT.sub("", raw)
    name = _BRACKETED.sub(" ", name)
    name = _VERSION.sub("", name)
    name = _TAGS.sub("", name)
    scene = " " not in name.strip() and len(re.findall(r"[._]", name)) >= 2
    name = re.sub(r"[_]+", " ", name)
    if " " not in name.strip() or scene:
        name = re.sub(r"[.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if scene:
        name = _SCENE_GROUP.sub("", name)
    return name or raw


def _names(game: Game) -> list[str]:
    seen: list[str] = []
    for n in (search_name(game.name), search_name(game.fs_name), game.name):
        if n and n not in seen:
            seen.append(n)
    return seen


def _find_igdb(game: Game) -> dict | None:
    names = _names(game)
    return best_match(query_variants(names), names[0], igdb_handler.search_games, lambda r: r.get("name", ""))


def _find_sgdb_id(game: Game) -> int | None:
    names = _names(game)
    match = best_match(query_variants(names), names[0], sgdb_handler.search_games, lambda r: r.get("name", ""))
    return match["id"] if match else None


def _apply_igdb(game: Game, igdb_id: int) -> bool:
    full = igdb_handler.get_game_by_id(igdb_id)
    if full is None:
        return False
    db_game_handler.update_game(
        game.id,
        {"igdb_id": igdb_id, "name": full.get("name") or game.name, "summary": full.get("summary"), "igdb_metadata": full},
    )
    return True


def scrape_game(game: Game) -> bool:
    """Fill in whatever metadata this game is still missing. A manually set
    igdb_id / sgdb_id is used directly instead of searching by name. Returns
    True if anything was applied."""
    applied = False

    if game.igdb_id:
        if not game.igdb_metadata:
            applied |= _apply_igdb(game, game.igdb_id)
    else:
        match = _find_igdb(game)
        if match:
            # Search results can come back with sparser nested fields
            # than a by-id fetch, so re-fetch the full record.
            if not _apply_igdb(game, match["id"]):
                db_game_handler.update_game(
                    game.id,
                    {
                        "igdb_id": match["id"],
                        "name": match.get("name") or game.name,
                        "summary": match.get("summary"),
                        "igdb_metadata": match,
                    },
                )
            applied = True

    if not game.cover_path:
        sgdb_id = game.sgdb_id or _find_sgdb_id(game)
        grids = sgdb_handler.get_grids(sgdb_id) if sgdb_id else []
        if grids:
            db_game_handler.update_game(game.id, {"cover_path": grids[0], "sgdb_id": sgdb_id})
            applied = True

    return applied


def refresh_game(game: Game, keep: frozenset[str] = frozenset()) -> bool:
    """Re-fetch everything from the providers after the match changed: IGDB
    record (summary, genres, screenshots, ...) and the SteamGridDB cover. A set
    igdb_id / sgdb_id is used directly, otherwise the current name is searched.
    Fields named in `keep` (edited by hand in the same request) are left alone.
    Returns True if anything was applied."""
    applied = False

    full = igdb_handler.get_game_by_id(game.igdb_id) if game.igdb_id else None
    if full is None and not game.igdb_id:
        match = _find_igdb(game)
        if match:
            full = igdb_handler.get_game_by_id(match["id"]) or match
    if full is not None:
        update = {"igdb_id": full["id"], "igdb_metadata": full}
        if "summary" not in keep:
            update["summary"] = full.get("summary")
        if "name" not in keep and full.get("name"):
            update["name"] = full["name"]
        db_game_handler.update_game(game.id, update)
        applied = True

    sgdb_id = game.sgdb_id or _find_sgdb_id(game)
    grids = sgdb_handler.get_grids(sgdb_id) if sgdb_id else []
    if grids:
        update = {"sgdb_id": sgdb_id}
        if "cover_path" not in keep:
            update["cover_path"] = grids[0]
        db_game_handler.update_game(game.id, update)
        applied = True

    return applied


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    total: int
    scraped: int


def scrape_library(library_id: int) -> ScrapeResult:
    games = db_game_handler.get_games_for_library(library_id)
    scraped = sum(1 for g in games if scrape_game(g))
    return ScrapeResult(total=len(games), scraped=scraped)
