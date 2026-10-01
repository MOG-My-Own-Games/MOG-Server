"""Best-effort bulk metadata fill, in the spirit of RomM's scan+scrape (search
a provider by name, take the top result, apply it) but intentionally simpler:
no fuzzy-match scoring, no per-platform ranking, first result wins. Good
enough for a PC library where names are usually close to exact already;
revisit if that turns out not to hold (see docs/TODO.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from handler.database import db_game_handler
from handler.metadata import igdb_handler, sgdb_handler
from models.game import Game


def scrape_game(game: Game) -> bool:
    """Fill in whatever metadata this game is still missing. Returns True if
    anything was applied."""
    applied = False

    if not game.igdb_id:
        results = igdb_handler.search_games(game.name)
        if results:
            top = results[0]
            db_game_handler.update_game(
                game.id,
                {
                    "igdb_id": top["id"],
                    "name": top.get("name") or game.name,
                    "summary": top.get("summary"),
                    "igdb_metadata": top,
                },
            )
            applied = True

    if not game.cover_path:
        grids = sgdb_handler.search_grids(game.name)
        if grids:
            db_game_handler.update_game(game.id, {"cover_path": grids[0]})
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
