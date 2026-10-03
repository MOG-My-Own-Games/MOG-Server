"""Best-effort bulk metadata fill, in the spirit of RomM's scan+scrape (search
a provider by name, take the top result, apply it) but intentionally simpler:
no fuzzy-match scoring, no per-platform ranking, first result wins. Good
enough for a PC library where names are usually close to exact already;
revisit if that turns out not to hold (see docs/TODO.md).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from handler.database import db_game_handler
from handler.metadata import igdb_handler, sgdb_handler
from logger.logger import log
from handler.name_matching import best_match, query_variants
from models.game import Game

_BRACKETED = re.compile(r"[\[(\{][^\])}]*[\])}]")
_VERSION = re.compile(r"[\s_.-]+v?\d+(\.\d+)+\b.*$", re.IGNORECASE)
# Release/packaging words that start the noise after the title; everything after one is dropped.
_TAGS = re.compile(
    r"[\s_.-]+(gog|repack|setup|installer|multi\d*|goty|drm[\s_.-]?free|proper|readnfo|internal|retail|"
    r"dvd\d*|x64|x86|win(32|64)|rip|cracked|incl|(build|update|patch|hotfix)[\s_.-]*v?\d+)\b.*$",
    re.IGNORECASE,
)
_ARCHIVE_EXT = re.compile(r"\.(exe|iso|zip|rar|7z|tar|gz)$", re.IGNORECASE)
# A scene release group: the last hyphen-joined token, shaped like a tag rather than a word
# (all caps, several capitals as in "TiNYiSO", or letters mixed with digits as in "razor1911").
_SCENE_GROUP = re.compile(r"(?<=\S)-((?=[A-Za-z0-9]*([A-Z].*[A-Z]|\d))[A-Za-z0-9]{2,})$")
_SPACED_GROUP = re.compile(r"\s+-\s+[A-Z0-9]{3,}$")


def search_names(raw: str) -> list[str]:
    """Folder/file name reduced to plausible game titles for provider search:
    extension, bracketed tags, version and release suffixes dropped, and dots
    and underscores read as spaces. A trailing `-GROUP` is removed from the first
    title; when one was removed, a second title keeps it as a word, in case the
    hyphen was part of the real name."""
    name = _ARCHIVE_EXT.sub("", raw)
    name = _BRACKETED.sub(" ", name)
    name = _VERSION.sub("", name)
    name = _TAGS.sub("", name)
    scene = " " not in name.strip() and len(re.findall(r"[._]", name)) >= 1
    name = re.sub(r"[_]+", " ", name)
    if " " not in name.strip() or scene:
        name = re.sub(r"[.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    stripped = _SPACED_GROUP.sub("", _SCENE_GROUP.sub("", name)) if scene else _SPACED_GROUP.sub("", name)
    titles = [stripped or raw]
    if name != stripped and name:
        titles.append(re.sub(r"\s+", " ", name.replace("-", " ")).strip())
    return titles


def search_name(raw: str) -> str:
    return search_names(raw)[0]


def _names(game: Game) -> list[str]:
    seen: list[str] = []
    for source in (game.name, game.fs_name):
        for n in search_names(source):
            if n and n not in seen:
                seen.append(n)
    if game.name not in seen:
        seen.append(game.name)
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


def needs_scrape(game: Game) -> bool:
    return not game.missing_from_fs and (not game.igdb_id or not game.cover_path)


def scrape_library(library_id: int) -> ScrapeResult:
    games = [g for g in db_game_handler.get_games_for_library(library_id) if not g.missing_from_fs]
    scraped = 0
    for game in games:
        try:
            scraped += scrape_game(game)
        except Exception as e:  # noqa: BLE001 - one bad game must not stop the rest
            log.warning(f"Scrape of {game.name!r} failed: {e}")
    return ScrapeResult(total=len(games), scraped=scraped)


_scraping: set[int] = set()
_scraping_lock = threading.Lock()


def scrape_library_in_background(library_id: int) -> None:
    """Auto-match after a scan, so duplicates group without scraping each game
    by hand. A library already being scraped is left to the running pass."""
    with _scraping_lock:
        if library_id in _scraping:
            return
        _scraping.add(library_id)
    try:
        scrape_library(library_id)
    finally:
        with _scraping_lock:
            _scraping.discard(library_id)
