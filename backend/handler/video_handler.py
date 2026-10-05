"""A game's intro and gameplay videos: the ones IGDB lists (YouTube ids) when it has them, else found by
searching YouTube when the page is opened. The page embeds them; nothing is downloaded here.

RomM only takes IGDB's first video. The search is the extra step, so it is strict: a result has to be about
the game, say in its title what it is (an intro or trailer, a gameplay), and not be music, a commented or
"let's play" video, or in another language."""

from __future__ import annotations

import json
import re
import time
from typing import Any, NamedTuple

import httpx
from logger.logger import log

KINDS = ("intro", "gameplay")
_ID = re.compile(r"[\w-]{11}")

_GAMEPLAY = re.compile(r"\b(gameplay|game play|longplay|long play|walkthrough|playthrough)\b", re.I)
_CLEAN_GAMEPLAY = re.compile(
    r"\b(gameplay|game play|longplay|long play|no commentary|without commentary)\b", re.I
)
_PREFERRED = re.compile(r"\b(no commentary|without commentary|longplay|long play)\b", re.I)
_INTRO = re.compile(r"\b(intro|opening|trailer|teaser|announce\w*|launch|cinematic|title screen)\b", re.I)
_NO_COMMENTARY = re.compile(r"\b(no|without|free of) commentary\b|\bcommentary free\b", re.I)
# Music, commented or "let's play" videos, reviews and the like: not what a game's page wants to show.
_BANNED = re.compile(
    r"\b(sound ?tracks?|ost|music|bgm|themes?|songs?|remix(es)?|album|lyrics?|piano|cover|reactions?|reviews?"
    r"|tutorials?|guides?|how to|let'?s play|lets play|commentary|commentated|speed ?runs?|mods?|trainer"
    r"|cheats?|unboxing|streams?|live|top \d+|first (impressions?|look)|impressions?|hands.on|previews?)\b",
    re.I,
)
# Other languages, as tags or as a script that is not Latin: the page is in English.
_FOREIGN = re.compile(
    r"\b(ita|italiano|italian|espa[nñ]ol|spanish|esp|fran[cç]ais|french|deutsch|german|portugu[eê]s|brasil"
    r"|pt ?br|russian|comentado|commentaire|kommentar|commento)\b",
    re.I,
)
_NON_LATIN = re.compile(r"[^\u0000-ɏ -⁯℀-⅏]")

_RENDERER = '"videoRenderer":{"videoId":"'
_TITLE = re.compile(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"')
_LENGTH = re.compile(r'"lengthText":\{.*?"simpleText":"(\d[\d:]*)"', re.S)
_SEARCH_URL = "https://www.youtube.com/results"
_VIDEOS_ONLY = "EgIQAQ%3D%3D"  # YouTube's own filter for "type: video"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept-Language": "en-US,en;q=0.8",
    "Cookie": "CONSENT=YES+1",  # without it a European visitor gets the consent page instead of results
}
FOUND_TTL = 7 * 24 * 3600
MISSED_TTL = 6 * 3600
MAX_INTRO_SECONDS = 10 * 60  # a longer "intro" is a mix or a compilation
MIN_GAMEPLAY_SECONDS = 90  # a shorter "gameplay" is a clip

# (game id, kind) -> (when it was looked up, the entry or None for a miss)
_searched: dict[tuple[int, str], tuple[float, dict[str, Any] | None]] = {}


class Result(NamedTuple):
    video_id: str
    title: str
    seconds: int | None = None


def _entry(kind: str, video_id: str, title: str, source: str) -> dict[str, Any]:
    return {"kind": kind, "video_id": video_id, "title": title, "source": source}


def _banned(text: str) -> bool:
    text = _NO_COMMENTARY.sub(" ", text)  # "no commentary" is a recommendation, not a flaw
    return bool(_BANNED.search(text) or _FOREIGN.search(text) or _NON_LATIN.search(text))


def from_igdb(videos: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """IGDB's videos by kind, told apart by their names ("Gameplay", "Launch Trailer", "Intro"...). When
    none looks like an intro, the first one left over stands in for it: a video of the game beats none."""
    found: dict[str, dict[str, Any]] = {}
    left: list[dict[str, Any]] = []
    for video in videos or []:
        video_id, name = video.get("video_id") or "", video.get("name") or ""
        if not _ID.fullmatch(video_id) or _banned(name):
            continue
        if "gameplay" not in found and _GAMEPLAY.search(name):
            found["gameplay"] = _entry("gameplay", video_id, name, "igdb")
        elif "intro" not in found and _INTRO.search(name):
            found["intro"] = _entry("intro", video_id, name, "igdb")
        else:
            left.append(_entry("intro", video_id, name, "igdb"))
    if "intro" not in found and left:
        found["intro"] = left[0]
    return found


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _seconds(clock: str) -> int:
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def results_in(page: str) -> list[Result]:
    """The videos in a YouTube results page, in order, with their titles and lengths (None for a live one)."""
    out = []
    for chunk in page.split(_RENDERER)[1:]:
        video_id = chunk[:11]
        title = _TITLE.search(chunk)
        if not _ID.fullmatch(video_id) or not title:
            continue
        length = _LENGTH.search(chunk[:4000])
        out.append(Result(video_id, _unescape(title.group(1)), _seconds(length.group(1)) if length else None))
    return out


def _unescape(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


_EDITION_WORDS = {
    "classic",
    "remastered",
    "remaster",
    "edition",
    "definitive",
    "hd",
    "enhanced",
    "goty",
    "complete",
    "collection",
    "special",
    "the",
    "of",
    "and",
    "a",
    "an",
}
_ENOUGH_OF_THE_NAME = 0.7


def _words(text: str) -> set[str]:
    # A lone digit is kept: a 2 tells a sequel.
    return {w for w in _normal(text).split() if (len(w) > 1 or w.isdigit()) and w not in _EDITION_WORDS}


def _name_words(game_name: str) -> set[str]:
    return _words(re.split(r"\s[:\-]\s|:", game_name)[0]) or _words(game_name)


def _about(game_name: str, title: str) -> bool:
    """Whether a result is about this game: most of the words of its name, a subtitle after a colon aside,
    are in the title (a video of "Little Big Adventure 2" is one of its "Twinsen's ... 2 Classic")."""
    wanted = _name_words(game_name)
    return bool(wanted) and len(wanted & _words(title)) / len(wanted) >= _ENOUGH_OF_THE_NAME


def _without_the_name(game_name: str, title: str) -> str:
    """The title minus the game's own name, so that "Theme Hospital" is not banned for its word "theme"."""
    rest = title
    for word in sorted(_name_words(game_name), key=len, reverse=True):
        rest = re.sub(rf"\b{re.escape(word)}\b", " ", rest, flags=re.I)
    return rest


def _pick(game_name: str, kind: str, results: list[Result]) -> Result | None:
    usable = [r for r in results if _about(game_name, r.title)]
    usable = [r for r in usable if not _banned(_without_the_name(game_name, r.title))]
    if kind == "gameplay":
        usable = [
            r
            for r in usable
            if _CLEAN_GAMEPLAY.search(r.title) and (r.seconds is None or r.seconds >= MIN_GAMEPLAY_SECONDS)
        ]
        usable.sort(key=lambda r: not _PREFERRED.search(r.title))  # a bare one first, order kept
    else:
        usable = [
            r
            for r in usable
            if _INTRO.search(r.title)
            and not _GAMEPLAY.search(r.title)
            and (r.seconds is None or r.seconds <= MAX_INTRO_SECONDS)
        ]
    return usable[0] if usable else None


def _search(query: str) -> list[Result]:
    params = {"search_query": query, "sp": _VIDEOS_ONLY, "hl": "en", "gl": "US"}
    resp = httpx.get(_SEARCH_URL, params=params, headers=_HEADERS, timeout=8, follow_redirects=True)
    resp.raise_for_status()
    return results_in(resp.text)


def search_video(game_name: str, kind: str) -> dict[str, Any] | None:
    queries = (
        [f"{game_name} gameplay no commentary", f"{game_name} gameplay"]
        if kind == "gameplay"
        else [f"{game_name} intro", f"{game_name} official trailer"]
    )
    for query in queries:
        picked = _pick(game_name, kind, _search(query))
        if picked:
            return _entry(kind, picked.video_id, picked.title, "youtube")
    return None


def find_videos(
    game_id: int, game_name: str, igdb_videos: list[dict[str, Any]] | None, search=search_video, now=time.time
) -> list[dict]:
    """The game's videos in the order the page shows them: intro first, then gameplay. A kind IGDB does not
    have is searched for, and the answer (a miss too) is remembered for a while."""
    found = from_igdb(igdb_videos)
    for kind in KINDS:
        if kind in found:
            continue
        cached = _searched.get((game_id, kind))
        if cached and now() - cached[0] < (FOUND_TTL if cached[1] else MISSED_TTL):
            entry = cached[1]
        else:
            try:
                entry = search(game_name, kind)
            except (httpx.HTTPError, ValueError) as e:
                log.warning(f"Video search for {game_name!r} ({kind}) failed: {e}")
                continue  # not remembered: the next visit tries again
            _searched[(game_id, kind)] = (now(), entry)
        if entry:
            found[kind] = entry
    return [found[kind] for kind in KINDS if kind in found]
