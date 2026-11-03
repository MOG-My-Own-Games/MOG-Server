"""HTTP for the metadata providers, and reading the pages of the ones without an API (DLsite, itch.io, the Steam store): only
what a page declares about itself is taken, its Open Graph tags and the few elements around them."""

from __future__ import annotations

import html
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from logger.logger import log

USER_AGENT = "MOG-Server (game metadata lookup)"
TIMEOUT = 15.0

# The least time between two requests to a site, kept well inside what it allows: VNDB lets 200 requests in 5 minutes, and the
# Steam store, itch.io and DLsite publish no limit but block a client that goes fast. A site not listed is not paced (a RomM
# server is the person's own).
MIN_INTERVAL = {"api.vndb.org": 1.6, "store.steampowered.com": 1.5, "itch.io": 1.0, "www.dlsite.com": 1.0}
RETRY_AFTER_MAX = 30.0  # a Retry-After longer than this is not waited for
COOLDOWN_LIMITED = 600.0  # a site that refused (429, 403) is left alone for this long
COOLDOWN_FAILED = 120.0  # a site that failed (5xx, unreachable)

_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*["']([^"']*)["']""")

_lock = threading.Lock()
_next_slot: dict[str, float] = {}  # host -> when the next request to it may go
_cooldown: dict[str, float] = {}  # host -> when it may be asked again after it refused or failed
_sleep = time.sleep
_now = time.monotonic


class Unavailable(Exception):
    """The site did not answer (rate limited, blocked, down, unreachable). That says nothing about the game, so it is not a
    "not found": a caller must not note a miss for it."""


def _site(url: str) -> str | None:
    """The paced site a URL is on (itch.io for any game's own subdomain), or None for one that is not paced: a RomM server is the
    person's own, and a refusal from it is theirs to fix, not something to wait out."""
    host = urlsplit(url).hostname or ""
    return next((name for name in MIN_INTERVAL if host == name or host.endswith("." + name)), None)


def _pace(site: str | None) -> None:
    """Wait for this site's turn; a site that refused lately is not asked at all."""
    if site is None:
        return
    with _lock:
        now = _now()
        if _cooldown.get(site, 0.0) > now:
            raise Unavailable(f"{site} refused or failed a moment ago")
        slot = max(now, _next_slot.get(site, 0.0))
        _next_slot[site] = slot + MIN_INTERVAL[site]
    if slot > now:
        _sleep(slot - now)


def _retry_after(response: httpx.Response) -> float | None:
    try:
        wait = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return wait if 0 <= wait <= RETRY_AFTER_MAX else None


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json: Any = None,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response | None:
    """The response to a request, or None when the site answered that it has nothing (404 and the like). When it could not answer
    (429, 403, 5xx, unreachable) this raises Unavailable, after waiting once for the Retry-After a 429 asks for and pacing the
    requests to each site (MIN_INTERVAL). Nothing is logged but the address, so credentials in the body or headers never
    reach the log."""
    site, host = _site(url), urlsplit(url).hostname or url
    for attempt in (1, 2):
        _pace(site)
        try:
            response = httpx.request(
                method,
                url,
                params=params,
                data=data,
                json=json,
                cookies=cookies,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                timeout=TIMEOUT,
                follow_redirects=True,
            )
        except httpx.TransportError as e:
            log.debug(f"{method} {url} failed: {e}")
            _hold_off(site, COOLDOWN_FAILED)
            raise Unavailable(f"{host} is unreachable") from e
        code = response.status_code
        if code == 429 and attempt == 1 and (wait := _retry_after(response)) is not None:
            log.debug(f"{host} asked to wait {wait:g}s")
            _sleep(wait)
            continue
        if code in (403, 429):
            _hold_off(site, COOLDOWN_LIMITED)
            raise Unavailable(f"{host} answered {code}")
        if code >= 500:
            _hold_off(site, COOLDOWN_FAILED)
            raise Unavailable(f"{host} answered {code}")
        if code >= 400:
            log.debug(f"{method} {url} answered {code}")
            return None
        return response
    raise Unavailable(f"{host} kept asking to wait")  # unreachable: the second attempt always returns or raises


def _hold_off(site: str | None, seconds: float) -> None:
    if site is not None:
        with _lock:
            _cooldown[site] = _now() + seconds


def meta_tags(page: str) -> dict[str, str]:
    """The `<meta>` tags of a page as {property or name: content}, with the entities of the content decoded."""
    tags: dict[str, str] = {}
    for tag in _TAG.findall(page):
        attrs = {name.lower(): value for name, value in _ATTR.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            tags[key] = html.unescape(attrs["content"])
    return tags


def text_of(fragment: str) -> str:
    """The text inside an HTML fragment, tags removed and entities decoded, with its spaces collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())
