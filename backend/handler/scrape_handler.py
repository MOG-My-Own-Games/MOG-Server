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
from models.game import Game

_BRACKETED = re.compile(r"[\[(\{][^\])}]*[\])}]")
_VERSION = re.compile(r"[\s_.-]+v?\d+(\.\d+)+\b.*$", re.IGNORECASE)
_TAGS = re.compile(r"[\s_.-]+(gog|repack|setup|installer|multi\d*|goty|drm[\s_.-]?free)\b.*$", re.IGNORECASE)
_ARCHIVE_EXT = re.compile(r"\.(exe|iso|zip|rar|7z|tar|gz)$", re.IGNORECASE)


def search_name(raw: str) -> str:
    """Folder/file name reduced to a plausible game title for provider search
    (extension, bracketed tags, version and release-group suffixes dropped)."""
    name = _ARCHIVE_EXT.sub("", raw)
    name = _BRACKETED.sub(" ", name)
    name = _VERSION.sub("", name)
    name = _TAGS.sub("", name)
    if " " not in name.strip():
        name = re.sub(r"[_.]+", " ", name)
    name = re.sub(r"[_]+", " ", name)
    return re.sub(r"\s+", " ", name).strip() or raw


def _candidates(game: Game) -> list[str]:
    seen: list[str] = []
    for n in (game.name, search_name(game.name), search_name(game.fs_name)):
        if n and n not in seen:
            seen.append(n)
    return seen


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
        for query in _candidates(game):
            results = igdb_handler.search_games(query)
            if results:
                # Search results can come back with sparser nested fields
                # than a by-id fetch, so re-fetch the full record.
                if not _apply_igdb(game, results[0]["id"]):
                    full = results[0]
                    db_game_handler.update_game(
                        game.id,
                        {
                            "igdb_id": full["id"],
                            "name": full.get("name") or game.name,
                            "summary": full.get("summary"),
                            "igdb_metadata": full,
                        },
                    )
                applied = True
                break

    if not game.cover_path:
        sgdb_id = game.sgdb_id
        if not sgdb_id:
            for query in _candidates(game):
                sgdb_id = sgdb_handler.search_game_id(query)
                if sgdb_id:
                    break
        grids = sgdb_handler.get_grids(sgdb_id) if sgdb_id else []
        if grids:
            db_game_handler.update_game(game.id, {"cover_path": grids[0], "sgdb_id": sgdb_id})
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
