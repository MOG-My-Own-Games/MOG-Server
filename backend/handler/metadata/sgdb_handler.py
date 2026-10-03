"""Minimal SteamGridDB client: name search for cover art.

Phase 1 scope: search by name, return grid URLs. See igdb_handler.py's own
note on scope - no fuzzy matching yet.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from config import STEAMGRIDDB_API_KEY
from handler.database import db_settings_handler
from logger.logger import log

_API_BASE = "https://www.steamgriddb.com/api/v2"


def _api_key() -> str | None:
    """DB-stored key (set via Settings in the web UI) takes priority over
    the env var, which stays as the bootstrap/.env-only fallback."""
    return db_settings_handler.get_settings().steamgriddb_api_key or STEAMGRIDDB_API_KEY


def validate_key() -> bool:
    """Whether the configured key actually authenticates - used by Settings
    to show a pass/fail indicator, not to gate a real request."""
    api_key = _api_key()
    if not api_key:
        return False
    try:
        resp = httpx.get(
            f"{_API_BASE}/search/autocomplete/test",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _get(path: str, api_key: str) -> list:
    resp = httpx.get(f"{_API_BASE}/{path}", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", [])


def search_games(name: str) -> list[dict]:
    """SteamGridDB autocomplete results (id, name, ...), best first."""
    api_key = _api_key()
    if not api_key:
        return []
    try:
        return _get(f"search/autocomplete/{quote(name, safe='')}", api_key)
    except httpx.HTTPError as e:
        log.warning(f"SteamGridDB search for {name!r} failed: {e}")
        return []


def search_game_id(name: str) -> int | None:
    """SteamGridDB id of the first name match, or None."""
    results = search_games(name)
    return results[0]["id"] if results else None


def get_grids(sgdb_id: int, limit: int = 10) -> list[str]:
    """Grid image URLs for a known SteamGridDB game id."""
    api_key = _api_key()
    if not api_key:
        return []
    try:
        return [g["url"] for g in _get(f"grids/game/{sgdb_id}", api_key)[:limit]]
    except httpx.HTTPError as e:
        log.warning(f"SteamGridDB grids for id {sgdb_id} failed: {e}")
        return []


def search_grids(name: str, limit: int = 10) -> list[str]:
    """Search SteamGridDB by name and return grid image URLs for the first
    matching game."""
    sgdb_id = search_game_id(name)
    return get_grids(sgdb_id, limit) if sgdb_id else []
