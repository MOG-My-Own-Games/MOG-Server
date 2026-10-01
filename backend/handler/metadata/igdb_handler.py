"""Minimal IGDB client: OAuth token + name search.

Phase 1 scope: search by name and let the caller (an endpoint, a scan) pick
a result; the whole matched object (summary, genres, screenshots, age
ratings, DLCs/expansions/remakes, player info - see _GAME_FIELDS) is stored
as-is on Game.igdb_metadata. No fuzzy-match scoring or platform-aware
ranking yet (RomM's igdb_handler.py does a lot more of that) - see
docs/TODO.md.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from config import IGDB_CLIENT_ID, IGDB_CLIENT_SECRET
from handler.database import db_settings_handler
from logger.logger import log

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_API_BASE = "https://api.igdb.com/v4"

# Confirmed against the official Game endpoint field list
# (https://api-docs.igdb.com/#game): summary/storyline, genres, screenshots,
# age_ratings, player/multiplayer info, and the DLC/expansion/remake/remaster
# relations are all real fields there.
_GAME_FIELDS = (
    "id,name,summary,storyline,cover.url,first_release_date,"
    "genres.name,"
    "screenshots.url,"
    "age_ratings.rating,age_ratings.category,"
    "player_perspectives.name,"
    "game_modes.name,"
    "involved_companies.company.name,involved_companies.developer,involved_companies.publisher,"
    "dlcs.name,dlcs.cover.url,"
    "expansions.name,expansions.cover.url,"
    "remakes.name,remasters.name,"
    "parent_game.name,"
    "multiplayer_modes.onlinecoop,multiplayer_modes.offlinecoop,"
    "multiplayer_modes.onlinemax,multiplayer_modes.offlinemax"
)

_token: str | None = None
_token_expires_at: float = 0.0


def _credentials() -> tuple[str | None, str | None]:
    """DB-stored keys (set via Settings in the web UI) take priority over
    the env vars, which stay as the bootstrap/.env-only fallback."""
    settings = db_settings_handler.get_settings()
    client_id = settings.igdb_client_id or IGDB_CLIENT_ID
    client_secret = settings.igdb_client_secret or IGDB_CLIENT_SECRET
    return client_id, client_secret


def _get_token() -> tuple[str, str] | None:
    """Returns (client_id, token) so callers always send a matching pair -
    a token cached from a since-changed client_id would otherwise send a
    now-mismatched Client-ID header alongside a still-valid bearer token."""
    global _token, _token_expires_at
    client_id, client_secret = _credentials()
    if not client_id or not client_secret:
        return None
    if _token and time.monotonic() < _token_expires_at:
        return client_id, _token
    try:
        resp = httpx.post(
            _TOKEN_URL,
            params={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        log.warning(f"IGDB token request failed: {e}")
        return None
    _token = data["access_token"]
    _token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 60
    return client_id, _token


def _query(body: str) -> list[dict[str, Any]]:
    resolved = _get_token()
    if resolved is None:
        return []
    client_id, token = resolved
    try:
        resp = httpx.post(
            f"{_API_BASE}/games",
            headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
            content=body,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        log.warning(f"IGDB query failed: {e}")
        return []


def search_games(name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search IGDB by name. Returns resolved game dicts (cover/screenshot
    URLs are already absolute, see _resolve_media)."""
    escaped = name.replace('"', '\\"')
    results = _query(f'search "{escaped}"; fields {_GAME_FIELDS}; limit {limit};')
    return [_resolve_media(g) for g in results]


def get_game_by_id(igdb_id: int) -> dict[str, Any] | None:
    """Full, current data for one game, used when applying a search result
    chosen from `search_games` - search results can omit nested fields
    IGDB didn't bother populating in list context; fetching by id directly
    is what RomM's own igdb_handler.py does for this same reason."""
    results = _query(f"fields {_GAME_FIELDS}; where id = {igdb_id};")
    return _resolve_media(results[0]) if results else None


def cover_url(igdb_image_url: str | None, size: str = "cover_big") -> str | None:
    """IGDB returns image URLs as `//images.igdb.com/.../t_thumb/<id>.jpg`;
    swap the thumbnail size for a larger one and make the URL absolute."""
    if not igdb_image_url:
        return None
    url = igdb_image_url.replace("t_thumb", f"t_{size}")
    return f"https:{url}" if url.startswith("//") else url


def _resolve_media(game: dict[str, Any]) -> dict[str, Any]:
    """Resolve every nested `*.url` (cover, screenshots, dlc/expansion
    covers) to an absolute, browser-ready URL in place, so nothing storing
    or displaying this dict needs to know IGDB's own thumbnail-size
    convention."""
    if "cover" in game and game["cover"]:
        game["cover"]["url"] = cover_url(game["cover"].get("url"), "cover_big")
    for shot in game.get("screenshots") or []:
        shot["url"] = cover_url(shot.get("url"), "screenshot_big")
    for key in ("dlcs", "expansions"):
        for item in game.get(key) or []:
            if item.get("cover"):
                item["cover"]["url"] = cover_url(item["cover"].get("url"), "cover_big")
    return game
