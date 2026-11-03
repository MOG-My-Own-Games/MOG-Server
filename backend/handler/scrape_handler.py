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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from handler.database import db_game_handler
from handler.filesystem.fs_tags import ARCHIVE_EXT, name_without_tags
from handler.imports import adopt_placeholders, merged_into
from handler.notifications import notify_games_added
from handler import cover_art
from handler import media as media_handler
from handler import media_uploads
from handler import video_handler
from handler.metadata import hltb_handler, igdb_handler, providers, sgdb_handler, web
from logger.logger import log
from handler.name_matching import best_match, query_variants
from models.game import Game
from utils import image_cache
from models.library import Library

_BRACKETED = re.compile(r"[\[(\{][^\])}]*[\])}]")
# A year in parentheses, "(1997)": kept as written, since it tells apart games that share a name (two Dooms).
_YEAR_TAG = re.compile(r"\(\s*(19\d{2}|20[0-2]\d)\s*\)")
_VERSION = re.compile(r"[\s_.-]+v?\d+(\.\d+)+\b.*$", re.IGNORECASE)
# Release/packaging words that start the noise after the title; everything after one is dropped.
_TAGS = re.compile(
    r"[\s_.-]+(gog|setup|installer|multi\d*|goty|drm[\s_.-]?free|proper|readnfo|internal|retail|"
    r"dvd\d*|x64|x86|win(32|64)|rip|cracked|incl|(build|update|patch|hotfix)[\s_.-]*v?\d+)\b.*$",
    re.IGNORECASE,
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
# A provider that found nothing for a game is left alone by the automatic passes for this long: an adult title or a
# new release is still missing next week, but a search that failed (provider down) must not stick.
MISS_RETRY = timedelta(days=7)


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
    name = ARCHIVE_EXT.sub("", raw)
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
    # A folder named "SamuraiSacrament" is "Samurai Sacrament" to a provider: tried after the name as it is.
    for title in list(titles):
        spaced = _CAMEL_JOIN.sub(" ", title)
        if " " not in title and spaced != title and spaced not in titles and all(len(word) >= _CAMEL_WORD_MIN for word in spaced.split()):
            titles.append(spaced)
    if year:  # after the version tail went, or it would take the year with it
        titles = [title if f"({year})" in title else f"{title} ({year})" for title in titles]
    return titles


def _has_default_name(game: Game) -> bool:
    """Whether the game still has the name a scan gave it (its folder's, with or without the tags): no match, and no one, has named it."""
    return game.name in (game.fs_name, name_without_tags(game.fs_name))


_CAMEL_JOIN = re.compile(r"(?<=[a-z])(?=[A-Z])")
_CAMEL_WORD_MIN = 3  # "TiNYiSO" is a tag-like word, not "Ti NYi SO"


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


def _missed(game: Game, provider: str) -> bool:
    """Whether `provider` searched for the game lately and found nothing (see MISS_RETRY)."""
    when = (game.provider_misses or {}).get(provider)
    try:
        return bool(when) and datetime.fromisoformat(when) > datetime.now(UTC) - MISS_RETRY
    except (TypeError, ValueError):  # a value that is not a time is no miss: the provider is asked again
        return False


def _dismissed(game: Game, provider: str) -> bool:
    """Whether a person removed the provider from the game (see remove_match): no scrape matches it again until they choose it."""
    when = (game.provider_misses or {}).get(provider)
    try:
        return bool(when) and datetime.fromisoformat(when) > datetime.now(UTC) + MISS_RETRY
    except (TypeError, ValueError):
        return False


def _note(misses: dict[str, str], provider: str, found: bool) -> None:
    """Record the outcome of a search in `misses`; the caller writes it with the game's other changes."""
    if found:
        misses.pop(provider, None)
    else:
        misses[provider] = datetime.now(UTC).isoformat()


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
    if match and not igdb_matched and "name" not in keep and _has_default_name(game) and match.get("name"):
        return {"name": match["name"]}
    return {}


PRIORITY = ("igdb", *(module.KEY for module in providers.ORDER))  # which provider's record is the game's metadata when several have it


def _ask(game: Game, modules: list, refetch: bool) -> list[tuple[str, bool, dict | None]]:
    """Each of `modules` asked about the game at once (every site has its own pace, so they do not wait for each other): the game
    read again by the id it already has with `refetch`, searched by name otherwise. [(key, searched, record or None)], with the
    providers that could not answer left out: nothing was learned about the game from them."""
    names = _names(game)

    def one(module) -> tuple[str, bool, dict | None] | None:
        ref = (game.provider_ids or {}).get(module.KEY) if refetch else None
        try:
            return module.KEY, ref is None, module.get(ref) if ref is not None else providers.find(module, names)
        except web.Unavailable as e:  # it could not answer: no miss, and the others are not held up
            log.debug(f"{module.KEY} did not answer for {game.name!r}: {e}")
        except Exception as e:  # noqa: BLE001 - a provider that answers oddly must not stop the others, nor leave a miss behind
            log.warning(f"{module.KEY} lookup for {game.name!r} failed: {e}")
        return None

    if not modules:
        return []
    with ThreadPoolExecutor(max_workers=len(modules), thread_name_prefix="providers") as pool:
        return [answer for answer in pool.map(one, modules) if answer is not None]


def _find_elsewhere(game: Game, misses: dict[str, str], refresh: bool = False) -> dict[str, dict]:
    """Every one of the other providers that has the game, asked together: {provider: its record}. A provider searched that has
    nothing is noted as a miss, so an automatic pass does not ask it again for a while. With `refresh` (a scrape a person asked
    for) a provider that missed lately is asked again, and one the game is matched with is read again by that id; only one the
    person removed from the game is left alone."""
    modules = [
        module
        for module in providers.ORDER
        if module.is_configured()
        and not _dismissed(game, module.KEY)
        and (refresh or not _missed(game, module.KEY))
    ]
    found: dict[str, dict] = {}
    for key, searched, record in _ask(game, modules, refetch=refresh):
        if searched:
            _note(misses, key, record is not None)
        if record is not None:
            found[key] = record
    return found


def _associate(game: Game, found: dict[str, dict], keep: frozenset[str] = frozenset()) -> dict:
    """What the providers (other than IGDB) that have the game change: their ids join the game's. A game with no source of its
    metadata takes the first of them by PRIORITY, and a source that is one of them has its record read again; the name and the
    summary come with the record (the name only while the game still has the one a scan gave it), except what `keep` names."""
    fresh = db_game_handler.get_game(game.id) or game
    update: dict = {"provider_ids": {**(fresh.provider_ids or {}), **{key: record["id"] for key, record in found.items()}}}
    source = fresh.metadata_source or next((key for key in PRIORITY if key in found), None)
    if source in found:
        rename = "name" not in keep and (_has_default_name(fresh) or fresh.metadata_source == source)
        meta = _match_update(fresh, source, found[source], rename=rename)
        update.update({field: value for field, value in meta.items() if field not in keep})
    return update


DISMISSED_FOR = timedelta(days=3650)  # a match a person removed is not searched again by the automatic passes (a scrape they ask for is)


def remove_match(game: Game, key: str) -> dict:
    """What taking away the game's match with a provider changes (`key` is igdb, sgdb, hltb or one of providers.ORDER): its id, and
    the metadata and summary it gave when it was the source (see promote_source for the next one). No scrape matches the provider
    again until a person chooses it."""
    update: dict = {}
    misses = dict(game.provider_misses or {})
    if key == "sgdb":
        update["sgdb_id"] = None
    elif key == "hltb":
        update.update(hltb_id=0, hltb_metadata={})  # 0: looked up, nothing found: not searched again
    else:
        update["provider_ids"] = {k: v for k, v in (game.provider_ids or {}).items() if k != key}
        if key == "igdb":
            update["igdb_id"] = None
        if game.metadata_source == key:
            if game.summary and game.summary == (game.igdb_metadata or {}).get("summary"):
                update["summary"] = None
            update.update(metadata_source=None, igdb_metadata={})
    misses[key] = (datetime.now(UTC) + DISMISSED_FOR).isoformat()
    update["provider_misses"] = misses
    return update


def promote_source(game: Game) -> dict:
    """When a game that still has matches has lost the source of its metadata (it was removed), the next provider by PRIORITY
    becomes it: its record is read, and gives the metadata and the summary the game has none of. {} when nothing is left."""
    if game.metadata_source:
        return {}
    for key in PRIORITY:
        ref = (game.provider_ids or {}).get(key)
        if ref is None:
            continue
        try:
            record = igdb_handler.get_game_by_id(ref) if key == "igdb" else providers.by_key(key).get(ref)
        except Exception as e:  # noqa: BLE001 - the next one is tried; the id stays, the record comes with a scrape
            log.warning(f"{key} record for {game.name!r} could not be read: {e}")
            continue
        if record is None:
            continue
        update = {"metadata_source": key, "igdb_metadata": record}
        if not game.summary and record.get("summary"):
            update["summary"] = record["summary"]
        return update
    return {}


def set_sgdb_game(game: Game, sgdb_id: int) -> None:
    """A SteamGridDB game a person picked: it is the game's, and the artwork is chosen again from it (uploads are kept)."""
    misses = {k: v for k, v in (game.provider_misses or {}).items() if k != "sgdb"}
    db_game_handler.update_game(game.id, {"sgdb_id": sgdb_id, "provider_misses": misses})
    fresh = db_game_handler.get_game(game.id) or game
    _store_media(fresh, sgdb_id, replace=True)


def set_match(game: Game, key: str, found: dict) -> None:
    """A match a person picked from one provider: the game is matched with it too (its id joins the others), it becomes the source
    of the metadata, name and summary, and the artwork is chosen again from it. The person's own uploads are kept."""
    fresh = db_game_handler.get_game(game.id) or game
    update = _match_update(fresh, key, found)
    update["provider_ids"] = {**(fresh.provider_ids or {}), key: found["id"]}
    update["provider_misses"] = {k: v for k, v in (fresh.provider_misses or {}).items() if k != key}  # a removal is undone by choosing it again
    db_game_handler.update_game(game.id, update)
    fresh = db_game_handler.get_game(game.id) or game
    _store_media(fresh, fresh.sgdb_id, replace=True)


def _match_update(game: Game, key: str, found: dict, rename: bool = True) -> dict:
    """What a record from `key` sets on the game as the source of its metadata: the record, and the name and summary it gives. The
    ids are the callers'. With `rename` off the game keeps its name (an earlier match already gave it one that is better than a
    romanised title)."""
    update = {
        "metadata_source": key,
        "igdb_metadata": found,
        "summary": found.get("summary"),
    }
    if rename:
        update["name"] = found.get("name") or game.name
    return update


def _apply_igdb(game: Game, igdb_id: int) -> bool:
    full = igdb_handler.get_game_by_id(igdb_id)
    if full is None:
        return False
    db_game_handler.update_game(
        game.id,
        {
            "igdb_id": igdb_id,
            "provider_ids": {**(game.provider_ids or {}), "igdb": igdb_id},
            "metadata_source": "igdb",
            "name": full.get("name") or game.name,
            "summary": full.get("summary"),
            "igdb_metadata": full,
        },
    )
    return True


def _find_hltb(game: Game, matched_name: str | None) -> dict | None:
    names = list(dict.fromkeys([matched_name, *_names(game)] if matched_name else _names(game)))
    return best_match(query_variants(names), names[0], hltb_handler.search_games, lambda r: r.get("name", ""))


def _fill_hltb(game: Game, matched_name: str | None = None, refresh: bool = False) -> bool:
    """Completion times from HowLongToBeat, for a game with an IGDB match only (`matched_name` is the name
    that match just gave it). hltb_id 0 records a lookup that found nothing, so a scan does not search again
    for it; a refresh does. HowLongToBeat being unreachable changes nothing. Returns True if times were stored."""
    if not hltb_handler.is_enabled() or not (game.igdb_id or game.metadata_source or matched_name) or _dismissed(game, "hltb"):
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


def _image_bytes(entry: dict | None) -> bytes | None:
    """The bytes of a chosen artwork (a provider's, through the cache, or an upload), or None when it cannot be had."""
    url = (entry or {}).get("url")
    if not url:
        return None
    path = media_uploads.path_for(url) if media_uploads.is_upload(url) else image_cache.cached_image(url)
    try:
        return path.read_bytes() if path is not None and path.is_file() else None
    except OSError:
        return None


def _tall_cover(game: Game, media: dict) -> dict | None:
    """The cover the cards show, when the one chosen is not as tall as they are (or there is none): the chosen art set whole on a 2:3
    canvas, or, with no cover, the banner or hero set that way, or a poster with the game's name. It is kept as an upload-like file
    of the game, with source `generated` and `from` the image it was made of. None when the cover is fine as it is."""
    cover = media.get("cover")
    if cover and cover.get("source") in (media_uploads.UPLOAD, cover_art.GENERATED):
        return None  # a person's own image is not changed; a generated one is already as tall as it should be
    base = cover or media.get("banner") or media.get("hero")
    data = _image_bytes(base)
    if cover and data is not None:
        size = cover_art.size_of(data)
        if size is None or cover_art.fits(*size):
            return None
    made = (cover_art.fit(data) if data is not None else None) or (None if cover else cover_art.poster(game.name))
    if made is None:
        return None  # a cover that could not be fetched stays what it was
    try:
        url = media_uploads.store(game.id, "cover", made)
    except media_uploads.UploadRefused as e:
        log.warning(f"Could not keep the cover made for {game.name!r}: {e}")
        return None
    return {"url": url, "source": cover_art.GENERATED, "from": (base or {}).get("url")}


def _store_media(game: Game, sgdb_id: int | None, replace: bool, keep: set[str] | frozenset[str] = frozenset()) -> bool:
    """Give the game the providers' own pick for each kind of artwork. With `replace` every kind
    not in `keep` is overwritten (a scrape the person asked for); without, only the kinds the game
    has no choice for yet (the automatic pass, which must not undo anyone's picks). cover_path
    follows the cover, which is made as tall as the cards when the chosen one is not (see _tall_cover).
    Returns True if anything was chosen."""
    fresh = db_game_handler.get_game(game.id) or game
    uploaded = media_uploads.uploaded_kinds(fresh.media)
    keep = keep | uploaded  # a person's own upload is not replaced by a scrape
    cleared = set(fresh.media_cleared or [])
    chosen = media_handler.defaults(media_handler.candidates(fresh, sgdb_id=sgdb_id))
    # A kind a person removed stays empty for the automatic pass; the scrape they ask for fills it again.
    chosen = {kind: pick for kind, pick in chosen.items() if kind not in uploaded and (replace or kind not in cleared)}
    current = dict(fresh.media or {})
    generated = current.get("cover") if (current.get("cover") or {}).get("source") == cover_art.GENERATED else None
    if generated and "cover" in chosen and (replace or chosen["cover"]["url"] != generated.get("from")):
        current.pop("cover")  # a real cover has come that the generated one was not made of
    merged = {**current, **{k: v for k, v in chosen.items() if k not in keep}} if replace else {**chosen, **current}
    tall = None
    if "cover" not in keep and ("cover" not in cleared or "cover" in chosen):
        tall = _tall_cover(fresh, merged)
        if tall is not None:
            merged["cover"] = tall
    if not chosen and merged == (fresh.media or {}):
        return False
    update: dict = {}
    refilled = set(chosen) - keep
    if replace and cleared & refilled:
        update["media_cleared"] = sorted(cleared - refilled)
    if merged != (fresh.media or {}):
        update["media"] = merged
    cover = merged.get("cover")
    if cover and cover["url"] != fresh.cover_path and (replace or not fresh.cover_path or tall is not None or generated is not None) and "cover" not in keep:
        update["cover_path"] = cover["url"]
    if generated and (merged.get("cover") or {}).get("url") != generated["url"]:
        media_uploads.remove(generated["url"])  # the one it was replaced by is another file
    if update:
        db_game_handler.update_game(game.id, update)
    return bool(chosen)  # a poster made for a game no provider has art for is not something a provider gave


def scrape_game(game: Game) -> bool:
    """Fill in whatever metadata this game is still missing. A manually set
    igdb_id / sgdb_id is used directly instead of searching by name. Returns
    True if anything was applied."""
    applied = False
    igdb_name = None
    igdb_matched = False
    misses = dict(game.provider_misses or {})
    unmatched = not game.igdb_id and not game.metadata_source

    if game.igdb_id:
        if not game.igdb_metadata:
            applied |= _apply_igdb(game, game.igdb_id)
    elif not game.metadata_source and igdb_handler.is_configured() and not _missed(game, "igdb"):
        match = _find_igdb(game)
        _note(misses, "igdb", match is not None)
        if match:
            igdb_name = match.get("name")
            igdb_matched = True
            # Search results can come back with sparser nested fields
            # than a by-id fetch, so re-fetch the full record.
            if not _apply_igdb(game, match["id"]):
                db_game_handler.update_game(
                    game.id,
                    {
                        "igdb_id": match["id"],
                        "provider_ids": {"igdb": match["id"]},
                        "metadata_source": "igdb",
                        "name": match.get("name") or game.name,
                        "summary": match.get("summary"),
                        "igdb_metadata": match,
                    },
                )
            applied = True

    # A game with no match at all is asked of every other provider that is on, together: it is matched with each that has it, and
    # the first of them by PRIORITY (IGDB's own record first of all) gives its metadata. A game that has a match is left as it is.
    if unmatched:
        found = _find_elsewhere(game, misses)
        if found:
            db_game_handler.update_game(game.id, _associate(game, found))
            igdb_name = igdb_name or next((found[key].get("name") for key in PRIORITY if key in found), None)
            applied = True

    if not game.cover_path or not game.media:
        sgdb_match = None
        if not game.sgdb_id and sgdb_handler.is_configured() and not _missed(game, "sgdb"):
            sgdb_match = _find_sgdb(game)
            _note(misses, "sgdb", sgdb_match is not None)
        sgdb_id = game.sgdb_id or (sgdb_match["id"] if sgdb_match else None)
        if sgdb_match:
            update = {"sgdb_id": sgdb_id}
            update.update(_sgdb_name(game, sgdb_match, igdb_matched=applied))
            db_game_handler.update_game(game.id, update)
        applied |= _store_media(game, sgdb_id, replace=False)

    applied |= _fill_hltb(game, igdb_name)
    if misses != (game.provider_misses or {}):
        db_game_handler.update_game(game.id, {"provider_misses": misses})
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
    misses = dict(game.provider_misses or {})

    full = igdb_handler.get_game_by_id(game.igdb_id) if game.igdb_id else None
    if full is None and not game.igdb_id and not _dismissed(game, "igdb"):
        match = _find_igdb(game)
        if igdb_handler.is_configured():
            _note(misses, "igdb", match is not None)
        if match:
            full = igdb_handler.get_game_by_id(match["id"]) or match
    if full is not None:
        update = {"igdb_id": full["id"], "provider_ids": {**(game.provider_ids or {}), "igdb": full["id"]}}
        if game.metadata_source in (None, "igdb"):  # a source a person chose (itch.io's, say) is not taken back by a scrape
            update.update(metadata_source="igdb", igdb_metadata=full)
            if "summary" not in keep:
                update["summary"] = full.get("summary")
            if "name" not in keep and full.get("name"):
                update["name"] = full["name"]
        db_game_handler.update_game(game.id, update)
        applied = True
    # Every other provider that is on is asked too, together: the game is matched with each that has it (read again by its id when
    # it is already matched with it).
    found = _find_elsewhere(game, misses, refresh=True)
    if found:
        db_game_handler.update_game(game.id, _associate(game, found, keep))
        applied = True

    searched = (rematch_cover or not game.sgdb_id) and not _dismissed(game, "sgdb")
    sgdb_match = _find_sgdb(game) if searched else None
    if searched and sgdb_handler.is_configured():
        _note(misses, "sgdb", sgdb_match is not None)
    sgdb_id = (sgdb_match["id"] if sgdb_match else None) or game.sgdb_id
    if sgdb_match:
        update = {"sgdb_id": sgdb_id}
        update.update(_sgdb_name(game, sgdb_match, igdb_matched=full is not None or bool(found), keep=keep))
        db_game_handler.update_game(game.id, update)
    applied |= _store_media(game, sgdb_id, replace=True, keep={"cover"} if "cover_path" in keep else set())

    matched_name = (full or {}).get("name") or next((found[key].get("name") for key in PRIORITY if key in found), None)
    applied |= _fill_hltb(game, matched_name, refresh=True)
    if misses != (game.provider_misses or {}):
        db_game_handler.update_game(game.id, {"provider_misses": misses})
    return applied


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    total: int
    scraped: int


def needs_scrape(game: Game) -> bool:
    if game.missing_from_fs and not game.imported:  # an imported game has no folder, but still wants its cover
        return False
    # A provider that is off or has no key has nothing to do, and one that found nothing lately is not asked again
    # (see MISS_RETRY): either way the game is not waiting on it, so an unmatched game does not keep the library scraping.
    # A game with a match from any provider is not waiting for another one (see scrape_game).
    matched = bool(game.igdb_id or game.metadata_source)
    metadata_pending = not matched and (
        (igdb_handler.is_configured() and not _missed(game, "igdb"))
        or any(module.is_configured() and not _missed(game, module.KEY) for module in providers.ORDER)
    )
    sgdb_pending = not game.sgdb_id and sgdb_handler.is_configured() and not _missed(game, "sgdb")
    cleared = set(game.media_cleared or [])
    media_pending = (not game.cover_path and "cover" not in cleared) or (
        (not game.cover_path or not game.media) and not cleared and (bool(game.sgdb_id) or sgdb_pending)
    )  # a game with no cover is given one (a poster at the least), whatever providers it has
    # hltb_id 0 is a lookup that found nothing, so only a game never looked up is still to do.
    hltb_pending = matched and game.hltb_id is None and hltb_handler.is_enabled()
    return metadata_pending or media_pending or hltb_pending


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
            adopt_placeholders(game.id)  # the match may have given it the name of a game a client imported
        except Exception as e:  # noqa: BLE001 - one bad game must not stop the rest
            log.warning(f"Scrape of {game.name!r} failed: {e}")
    warm_videos([g for g in db_game_handler.get_games_for_library(library_id) if not g.missing_from_fs])
    return ScrapeResult(total=len(games), scraped=scraped)


def warm_videos(games: list[Game]) -> None:
    """Have the videos of these games looked up in the background, from the metadata as it is now, so their
    pages do not wait for a search the first time they are opened."""
    try:
        video_handler.prefetch_in_background(
            (g.id, g.name, (g.igdb_metadata or {}).get("videos")) for g in games
        )
    except Exception as e:  # noqa: BLE001 - the scrape is done; this is only a head start
        log.warning(f"Could not start the video lookups: {e}")


def warm_all_videos() -> None:
    """The same for the whole library, at start: the games that were scanned before this existed."""
    warm_videos([g for g in db_game_handler.get_all_games() if not g.missing_from_fs])


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


def scrape_imported_in_background(game_id: int) -> None:
    """Match a game a client just imported, so its entry has a cover and metadata like any other."""

    def run() -> None:
        try:
            game = db_game_handler.get_game(game_id)
            if game is not None and needs_scrape(game):
                scrape_game(game)
        except Exception as e:  # noqa: BLE001 - the entry is usable without it
            log.warning(f"Scrape of the imported game {game_id} failed: {e}")

    threading.Thread(target=run, daemon=True, name=f"scrape-import-{game_id}").start()


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
        game_id = merged_into.get(game_id, game_id)  # a game merged into one a client imported is announced as that one
        game = db_game_handler.get_game(game_id)
        named.append((game_id, game.name if game else folder_name))
    try:
        notify_games_added(library, tuple(named))
    except Exception as e:  # noqa: BLE001 - the scan and the match are done; a notification must not undo them
        log.warning(f"Could not create the notification for the added games: {e}")
