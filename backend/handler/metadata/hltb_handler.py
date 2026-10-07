# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""HowLongToBeat: how long a game takes to finish. There is no official API, so this reads the
site's own search (a session minted at its /init, a rotating route) and the game page.

Anything that goes wrong reaching the site raises HLTBUnavailable, so "the site could not be asked"
is told apart from "the site has no such game" (an empty answer or None).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Final
from urllib.parse import urljoin

import httpx

import config
from logger.logger import log
from utils.hltb_search import (
    HLTB_BASE_URL,
    HLTB_SEARCH_URL,
    SESSION_MINT_SUFFIX,
    HLTBSession,
    base_headers,
    build_search_payload,
    parse_session,
    search_body,
    search_headers,
)

# HowLongToBeat publishes no limit, so stay well clear of being throttled.
MAX_REQUESTS_PER_SECOND: Final[int] = 3
# One attempt, plus one for a renewed session and one for a rate-limit backoff.
MAX_ATTEMPTS: Final[int] = 3
RATE_LIMIT_BACKOFF_SECONDS: Final[float] = 2
# A failed mint is not retried at once, so a blocked host is not hammered for every game of a scan.
SESSION_RETRY_SECONDS: Final[float] = 60
REDISCOVERY_RETRY_SECONDS: Final[float] = 600
TIMEOUT_SECONDS: Final[int] = 30

# The game page ships its record as JSON in the Next.js hydration payload.
_NEXT_DATA = re.compile(r"""<script[^>]*\bid=["']__NEXT_DATA__["'][^>]*>(.*?)</script>""", re.DOTALL)
# Next.js lists every route it serves as a plain literal in its build manifest.
_BUILD_MANIFEST = re.compile(r"""src=["'](?P<path>[^"']*/_next/static/[^"']+/_buildManifest\.js)["']""")
_API_ROUTE = re.compile(r"""["'](?P<route>/api/[^"']*)["']""")
# A term with many well known hits, so an empty answer means the route and not the term.
_PROBE_TERM = "mario"

# (key in the stored metadata, key in HowLongToBeat's record). Times are seconds.
_TIMES: Final = (
    ("main_story", "comp_main"),
    ("main_plus_extra", "comp_plus"),
    ("completionist", "comp_100"),
    ("all_styles", "comp_all"),
)

_state_lock = threading.RLock()
_pace_lock = threading.Lock()
_search_url = HLTB_SEARCH_URL
_session: HLTBSession | None = None
_session_retry_after = 0.0
_rediscovery_retry_after = 0.0
_next_request_at = 0.0


class HLTBUnavailable(Exception):
    """HowLongToBeat could not be asked: blocked, down, or its pages changed shape."""


def is_enabled() -> bool:
    return config.HLTB_ENABLED


def _pace() -> None:
    """Hold the caller back so requests keep under MAX_REQUESTS_PER_SECOND."""
    global _next_request_at
    with _pace_lock:
        now = time.monotonic()
        wait = _next_request_at - now
        _next_request_at = max(now, _next_request_at) + 1 / MAX_REQUESTS_PER_SECOND
    if wait > 0:
        time.sleep(wait)


def _release_year(value: object) -> int:
    """A release year: the search returns it as a number, the game page as an ISO date."""
    if isinstance(value, int):
        return value
    year = value.partition("-")[0] if isinstance(value, str) else ""
    return int(year) if year.isdigit() else 0


def _positive(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def extract_metadata(game: dict[str, Any]) -> dict[str, int]:
    """What is kept of a HowLongToBeat record: the four completion times in seconds, how many people
    reported each, the release year and the review score. Anything zero or missing is left out."""
    meta: dict[str, int] = {}
    for key, source in _TIMES:
        if seconds := _positive(game.get(source)):
            meta[key] = seconds
            if count := _positive(game.get(f"{source}_count")):
                meta[f"{key}_count"] = count
    if year := _release_year(game.get("release_world")):
        meta["release_year"] = year
    if score := _positive(game.get("review_score")):
        meta["review_score"] = score
    return meta


def has_times(meta: dict[str, Any] | None) -> bool:
    return bool(meta) and any(meta.get(key) for key, _ in _TIMES)


def _entry(game: dict[str, Any]) -> dict[str, Any]:
    return {"id": game["game_id"], "name": game.get("game_name", ""), "metadata": extract_metadata(game)}


# --- Endpoint discovery ---


def _manifest_routes(client: httpx.Client) -> list[str]:
    """The API routes that offer a session mint, the search-like ones first."""
    headers = base_headers()
    home = client.get(f"{HLTB_BASE_URL}/", headers=headers, timeout=15)
    home.raise_for_status()
    found = _BUILD_MANIFEST.search(home.text)
    if not found:
        return []
    manifest = client.get(urljoin(f"{HLTB_BASE_URL}/", found["path"]), headers=headers, timeout=15)
    manifest.raise_for_status()
    routes = list(dict.fromkeys(_API_ROUTE.findall(manifest.text)))
    return sorted((r for r in routes if f"{r}{SESSION_MINT_SUFFIX}" in routes), key=lambda r: "search" not in r)


def _serves_search(client: httpx.Client, search_url: str) -> bool:
    """Whether the route answers the search the handler sends, not just /init."""
    try:
        mint = client.get(
            f"{search_url}{SESSION_MINT_SUFFIX}", params={"t": int(time.time())}, headers=base_headers(), timeout=15
        )
        mint.raise_for_status()
        session = parse_session(mint.json())
        if session is None:
            return False
        reply = client.post(
            search_url,
            json=search_body(build_search_payload(_PROBE_TERM), session),
            headers=search_headers(session),
            timeout=TIMEOUT_SECONDS,
        )
        reply.raise_for_status()
        results = reply.json().get("data")
    except (httpx.HTTPError, ValueError, AttributeError):
        return False
    return isinstance(results, list) and bool(results) and all(
        isinstance(g, dict) and "game_id" in g and "game_name" in g for g in results
    )


def discover_endpoint() -> str | None:
    """The current search route, read from the site's own route table, or None."""
    try:
        with httpx.Client(follow_redirects=True) as client:
            for route in _manifest_routes(client):
                if _serves_search(client, f"{HLTB_BASE_URL}{route}"):
                    return f"{HLTB_BASE_URL}{route}"
    except httpx.HTTPError as e:
        log.warning(f"HowLongToBeat endpoint discovery failed: {e}")
    return None


def _rediscover_endpoint() -> bool:
    """Look for a new search route after the known one went missing. True if the route changed."""
    global _search_url, _session, _rediscovery_retry_after
    with _state_lock:
        now = time.monotonic()
        if now < _rediscovery_retry_after:
            return False
        _rediscovery_retry_after = now + REDISCOVERY_RETRY_SECONDS
        found = discover_endpoint()
        if not found or found == _search_url:
            return False
        log.info(f"HowLongToBeat search route moved to {found}")
        _search_url, _session = found, None
        return True


# --- Session and requests ---


def _ensure_session(renew: bool = False) -> HLTBSession | None:
    """The current session, minting one if there is none (or `renew`). None while a recent mint failed."""
    global _session, _session_retry_after
    with _state_lock:
        if renew:
            _session = None
        if _session is not None:
            return _session
        if time.monotonic() < _session_retry_after:
            return None
        _session_retry_after = time.monotonic() + SESSION_RETRY_SECONDS
        for _ in range(2):
            _pace()
            try:
                reply = httpx.get(
                    f"{_search_url}{SESSION_MINT_SUFFIX}",
                    params={"t": int(time.time())},
                    headers=base_headers(),
                    timeout=TIMEOUT_SECONDS,
                )
                reply.raise_for_status()
                _session = parse_session(reply.json())
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404 and _rediscover_endpoint():
                    continue
                log.warning(f"HowLongToBeat session request returned HTTP {e.response.status_code}")
            except (httpx.HTTPError, ValueError) as e:
                log.warning(f"HowLongToBeat session request failed: {e}")
            break
        if _session is not None:
            _session_retry_after = 0.0
        return _session


def _post_search(payload: dict[str, Any]) -> dict[str, Any]:
    """A search answer. A rejected session is renewed and a throttled call waited out."""
    for attempt in range(MAX_ATTEMPTS):
        session = _ensure_session()
        if session is None:
            raise HLTBUnavailable("no session could be minted")
        _pace()
        last = attempt == MAX_ATTEMPTS - 1
        try:
            reply = httpx.post(
                _search_url, json=search_body(payload, session), headers=search_headers(session), timeout=TIMEOUT_SECONDS
            )
            reply.raise_for_status()
            data = reply.json()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 403 and not last:
                _ensure_session(renew=True)
                continue
            if code == 429 and not last:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS)
                continue
            if code == 404 and not last and _rediscover_endpoint():
                continue
            raise HLTBUnavailable(f"search returned HTTP {code}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise HLTBUnavailable(f"search failed: {e}") from e
        if not isinstance(data, dict):
            raise HLTBUnavailable("search did not return a JSON object")
        return data
    raise HLTBUnavailable("search kept being rejected")


# --- Lookups ---


def search_games(name: str) -> list[dict]:
    """HowLongToBeat games matching the name as {id, name, metadata}, those with a completion time only."""
    if not is_enabled():
        return []
    games = _post_search(build_search_payload(name)).get("data")
    if not isinstance(games, list):
        raise HLTBUnavailable("search answer has no result list")
    found = [_entry(g) for g in games if isinstance(g, dict) and g.get("game_id")]
    return [g for g in found if has_times(g["metadata"])]


def get_game_by_id(hltb_id: int) -> dict | None:
    """One game as {id, name, metadata}, or None if it is gone or has no time. The API only searches, so
    this reads the record the game page ships to the browser."""
    if not is_enabled():
        return None
    _pace()
    try:
        page = httpx.get(
            f"{HLTB_BASE_URL}/game/{hltb_id}", headers=base_headers(), follow_redirects=True, timeout=TIMEOUT_SECONDS
        )
        if page.status_code == 404:
            return None
        page.raise_for_status()
    except httpx.HTTPError as e:
        raise HLTBUnavailable(f"game page failed: {e}") from e
    found = _NEXT_DATA.search(page.text)
    try:
        games = json.loads(found[1])["props"]["pageProps"]["game"]["data"]["game"] if found else None
    except (ValueError, KeyError, TypeError) as e:
        raise HLTBUnavailable(f"game page has an unexpected shape: {e}") from e
    if not isinstance(games, list):
        raise HLTBUnavailable("game page carries no game records")
    if not games or not isinstance(games[0], dict) or not games[0].get("game_id"):
        return None
    entry = _entry(games[0])
    return entry if has_times(entry["metadata"]) else None
