"""Best-effort bulk metadata fill, in the spirit of RomM's scan+scrape (search
a provider by name, take the top result, apply it) but intentionally simpler:
no fuzzy-match scoring, no per-platform ranking, first result wins. Good
enough for a PC library where names are usually close to exact already;
revisit if that turns out not to hold (see docs/TODO.md).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

from handler.database import db_game_handler
from handler.notifications import notify_games_added
from handler import media as media_handler
from handler.metadata import hltb_handler, igdb_handler, sgdb_handler
from logger.logger import log
from handler.name_matching import best_match, query_variants
from models.game import Game
from models.library import Library

_BRACKETED = re.compile(r"[\[(\{][^\])}]*[\])}]")
# A year in parentheses, "(1997)": kept as written, since it tells apart games that share a name (two Dooms).
_YEAR_TAG = re.compile(r"\(\s*(19\d{2}|20[0-2]\d)\s*\)")
_VERSION = re.compile(r"[\s_.-]+v?\d+(\.\d+)+\b.*$", re.IGNORECASE)
# Release/packaging words that start the noise after the title; everything after one is dropped.
_TAGS = re.compile(
    r"[\s_.-]+(gog|repack|setup|installer|multi\d*|goty|drm[\s_.-]?free|proper|readnfo|internal|retail|"
    r"dvd\d*|x64|x86|win(32|64)|rip|cracked|incl|(build|update|patch|hotfix)[\s_.-]*v?\d+)\b.*$",
    re.IGNORECASE,
)
_ARCHIVE_EXT = re.compile(
    r"\.(exe|iso|zip|rar|7z|tar|gz|sh|run|bin|msi|appimage|pkg|deb|dmg|cue|img|mdf|nrg|chd)$", re.IGNORECASE
)
# "setup_game_name_2.0_(12345).exe": the installer's own word in front of the title.
_LEADING_SETUP = re.compile(r"^(setup|install(er)?)[\s_.-]+(?=\S)", re.IGNORECASE)
# A piece of a version or build number left behind: "1", "0", "9a", "v2", "48364".
_VERSION_PART = re.compile(r"^v?\d+[a-z]?$", re.IGNORECASE)
# What marks such a trailing run as a version and not part of the title ("Cyberpunk 2077", "Jazz Jackrabbit 2"
# keep their number): three parts or more ("1 0 9a"), or one that is a build id (five digits or more).
_VERSION_RUN_LENGTH = 3
_BUILD_ID_DIGITS = 5
# A scene release group: the last hyphen-joined token, shaped like a tag rather than a word
# (all caps, several capitals as in "TiNYiSO", or letters mixed with digits as in "razor1911").
_SCENE_GROUP = re.compile(r"(?<=\S)-((?=[A-Za-z0-9]*([A-Z].*[A-Z]|\d))[A-Za-z0-9]{2,})$")
_SPACED_GROUP = re.compile(r"\s+-\s+[A-Z0-9]{3,}$")


def _drop_version_tail(name: str) -> str:
    """The title without the version and build numbers a release name ends with ("lost ruins 1 0 9a 48364" is
    "lost ruins"). A single trailing number stays: it is usually a sequel or a year in the title."""
    words = name.split()
    start = len(words)
    while start > 1 and _VERSION_PART.match(words[start - 1]):
        start -= 1
    run = words[start:]
    has_build_id = any(w.isdigit() and len(w) >= _BUILD_ID_DIGITS for w in run)
    if run and (len(run) >= _VERSION_RUN_LENGTH or has_build_id):
        return " ".join(words[:start])
    return name


def search_names(raw: str) -> list[str]:
    """Folder/file name reduced to plausible game titles for provider search:
    extension, bracketed tags, version and release suffixes dropped, and dots
    and underscores read as spaces. A trailing `-GROUP` is removed from the first
    title; when one was removed, a second title keeps it as a word, in case the
    hyphen was part of the real name."""
    name = _ARCHIVE_EXT.sub("", raw)
    # Underscores read as spaces from the start: to the patterns below an underscore is part of a word, so
    # "_goty_" or "_setup_" would not be seen as a tag.
    scene = " " not in name.strip() and len(re.findall(r"[._]", name)) >= 1
    name = _LEADING_SETUP.sub("", re.sub(r"_+", " ", name))
    year = (_YEAR_TAG.findall(name) or [""])[0]
    name = _BRACKETED.sub(" ", _YEAR_TAG.sub(" ", name))
    name = _VERSION.sub("", name)
    name = _TAGS.sub("", name)
    if " " not in name.strip() or scene:
        name = re.sub(r"[.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    stripped = _SPACED_GROUP.sub("", _SCENE_GROUP.sub("", name)) if scene else _SPACED_GROUP.sub("", name)
    titles = [_drop_version_tail(stripped) if stripped else raw]
    if name != stripped and name:
        titles.append(_drop_version_tail(re.sub(r"\s+", " ", name.replace("-", " ")).strip()))
    if year:  # after the version tail went, or it would take the year with it
        titles = [title if f"({year})" in title else f"{title} ({year})" for title in titles]
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


def _find_sgdb(game: Game) -> dict | None:
    names = _names(game)
    return best_match(query_variants(names), names[0], sgdb_handler.search_games, lambda r: r.get("name", ""))


def _find_sgdb_id(game: Game) -> int | None:
    match = _find_sgdb(game)
    return match["id"] if match else None


def _sgdb_name(game: Game, match: dict | None, igdb_matched: bool, keep: frozenset[str] = frozenset()) -> dict:
    """With no IGDB match to name it, a game still called by its raw file or folder
    name takes the name of the SteamGridDB game it matched."""
    if match and not igdb_matched and "name" not in keep and game.name == game.fs_name and match.get("name"):
        return {"name": match["name"]}
    return {}


def _apply_igdb(game: Game, igdb_id: int) -> bool:
    full = igdb_handler.get_game_by_id(igdb_id)
    if full is None:
        return False
    db_game_handler.update_game(
        game.id,
        {"igdb_id": igdb_id, "name": full.get("name") or game.name, "summary": full.get("summary"), "igdb_metadata": full},
    )
    return True


def _find_hltb(game: Game, matched_name: str | None) -> dict | None:
    names = list(dict.fromkeys([matched_name, *_names(game)] if matched_name else _names(game)))
    return best_match(query_variants(names), names[0], hltb_handler.search_games, lambda r: r.get("name", ""))


def _fill_hltb(game: Game, matched_name: str | None = None, refresh: bool = False) -> bool:
    """Completion times from HowLongToBeat, for a game with an IGDB match only (`matched_name` is the name
    that match just gave it). hltb_id 0 records a lookup that found nothing, so a scan does not search again
    for it; a refresh does. HowLongToBeat being unreachable changes nothing. Returns True if times were stored."""
    if not hltb_handler.is_enabled() or not (game.igdb_id or matched_name):
        return False
    if not refresh and (game.hltb_id == 0 or hltb_handler.has_times(game.hltb_metadata)):
        return False
    try:
        found = hltb_handler.get_game_by_id(game.hltb_id) if game.hltb_id else _find_hltb(game, matched_name)
    except hltb_handler.HLTBUnavailable as e:
        log.warning(f"HowLongToBeat lookup for {game.name!r} failed: {e}")
        return False
    if found is None:
        if game.hltb_id is None:
            db_game_handler.update_game(game.id, {"hltb_id": 0})
        return False
    db_game_handler.update_game(game.id, {"hltb_id": found["id"], "hltb_metadata": found["metadata"]})
    return True


def _store_media(game: Game, sgdb_id: int | None, replace: bool, keep: set[str] | frozenset[str] = frozenset()) -> bool:
    """Give the game the providers' own pick for each kind of artwork. With `replace` every kind
    not in `keep` is overwritten (a scrape the person asked for); without, only the kinds the game
    has no choice for yet (the automatic pass, which must not undo anyone's picks). cover_path
    follows the cover. Returns True if anything was chosen."""
    fresh = db_game_handler.get_game(game.id) or game
    chosen = media_handler.defaults(media_handler.candidates(fresh, sgdb_id=sgdb_id))
    if not chosen:
        return False
    current = dict(fresh.media or {})
    merged = {**current, **{k: v for k, v in chosen.items() if k not in keep}} if replace else {**chosen, **current}
    update: dict = {}
    if merged != current:
        update["media"] = merged
    cover = merged.get("cover")
    if cover and cover["url"] != fresh.cover_path and (replace or not fresh.cover_path) and "cover" not in keep:
        update["cover_path"] = cover["url"]
    if update:
        db_game_handler.update_game(game.id, update)
    return True


def scrape_game(game: Game) -> bool:
    """Fill in whatever metadata this game is still missing. A manually set
    igdb_id / sgdb_id is used directly instead of searching by name. Returns
    True if anything was applied."""
    applied = False
    igdb_name = None

    if game.igdb_id:
        if not game.igdb_metadata:
            applied |= _apply_igdb(game, game.igdb_id)
    else:
        match = _find_igdb(game)
        if match:
            igdb_name = match.get("name")
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

    if not game.cover_path or not game.media:
        sgdb_match = None if game.sgdb_id else _find_sgdb(game)
        sgdb_id = game.sgdb_id or (sgdb_match["id"] if sgdb_match else None)
        if sgdb_match:
            update = {"sgdb_id": sgdb_id}
            update.update(_sgdb_name(game, sgdb_match, igdb_matched=applied))
            db_game_handler.update_game(game.id, update)
        applied |= _store_media(game, sgdb_id, replace=False)

    applied |= _fill_hltb(game, igdb_name)
    return applied


def refresh_game(game: Game, keep: frozenset[str] = frozenset(), rematch_cover: bool = False) -> bool:
    """Re-fetch everything from the providers after the match changed: IGDB
    record (summary, genres, screenshots, ...) and the SteamGridDB cover. A set
    igdb_id / sgdb_id is used directly, otherwise the current name is searched.
    Fields named in `keep` (edited by hand in the same request) are left alone.
    With `rematch_cover` the SteamGridDB game is searched by name again even when an
    id is stored, so a cover matched to the wrong game is replaced rather than
    refetched from it. Returns True if anything was applied."""
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

    sgdb_match = _find_sgdb(game) if rematch_cover or not game.sgdb_id else None
    sgdb_id = (sgdb_match["id"] if sgdb_match else None) or game.sgdb_id
    if sgdb_match:
        update = {"sgdb_id": sgdb_id}
        update.update(_sgdb_name(game, sgdb_match, igdb_matched=full is not None, keep=keep))
        db_game_handler.update_game(game.id, update)
    applied |= _store_media(game, sgdb_id, replace=True, keep={"cover"} if "cover_path" in keep else set())

    applied |= _fill_hltb(game, (full or {}).get("name"), refresh=True)
    return applied


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    total: int
    scraped: int


def needs_scrape(game: Game) -> bool:
    if game.missing_from_fs:
        return False
    # hltb_id 0 is a lookup that found nothing, so only a game never looked up is still to do.
    hltb_pending = bool(game.igdb_id) and game.hltb_id is None and hltb_handler.is_enabled()
    return not game.igdb_id or not game.cover_path or not game.media or hltb_pending


def scrape_library(library_id: int, refresh: bool = False) -> ScrapeResult:
    """Fill what is missing for every present game; with `refresh`, re-fetch
    everything (metadata and cover) instead."""
    games = [g for g in db_game_handler.get_games_for_library(library_id) if not g.missing_from_fs]

    def fetch(game: Game) -> bool:
        return refresh_game(game, rematch_cover=True) if refresh else scrape_game(game)

    scraped = 0
    for game in games:
        try:
            scraped += fetch(game)
        except Exception as e:  # noqa: BLE001 - one bad game must not stop the rest
            log.warning(f"Scrape of {game.name!r} failed: {e}")
    return ScrapeResult(total=len(games), scraped=scraped)


_scraping: set[int] = set()
_scraping_lock = threading.Lock()
WAIT_FOR_RUNNING_PASS = 600  # seconds


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


def scrape_and_announce(library: Library, new_games: tuple[tuple[int, str], ...]) -> None:
    """After a scan: match the games it added, then tell the users about them under the names the match found.
    A pass already running for the library is waited for, so the games are named by it, not by their folders."""
    deadline = time.monotonic() + WAIT_FOR_RUNNING_PASS
    while time.monotonic() < deadline:
        with _scraping_lock:
            if library.id not in _scraping:
                break
        time.sleep(1)
    scrape_library_in_background(library.id)
    if not new_games:
        return
    named = []
    for game_id, folder_name in new_games:
        game = db_game_handler.get_game(game_id)
        named.append((game_id, game.name if game else folder_name))
    try:
        notify_games_added(library, tuple(named))
    except Exception as e:  # noqa: BLE001 - the scan and the match are done; a notification must not undo them
        log.warning(f"Could not create the notification for the added games: {e}")
