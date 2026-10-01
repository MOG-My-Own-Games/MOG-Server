"""Minimal SteamGridDB client: name search for cover art.

Phase 1 scope: search by name, return grid URLs. See igdb_handler.py's own
note on scope - no fuzzy matching yet.
"""

from __future__ import annotations

import httpx

from config import STEAMGRIDDB_API_KEY
from handler.database import db_settings_handler
from logger.logger import log

_API_BASE = "https://www.steamgriddb.com/api/v2"


def _api_key() -> str | None:
    """DB-stored key (set via Settings in the web UI) takes priority over
    the env var, which stays as the bootstrap/.env-only fallback."""
    return db_settings_handler.get_settings().steamgriddb_api_key or STEAMGRIDDB_API_KEY


def search_grids(name: str, limit: int = 10) -> list[str]:
    """Search SteamGridDB by name and return grid image URLs for the first
    matching game."""
    api_key = _api_key()
    if not api_key:
        return []
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        search_resp = httpx.get(f"{_API_BASE}/search/autocomplete/{name}", headers=headers, timeout=15)
        search_resp.raise_for_status()
        results = search_resp.json().get("data", [])
        if not results:
            return []
        game_id = results[0]["id"]

        grids_resp = httpx.get(f"{_API_BASE}/grids/game/{game_id}", headers=headers, timeout=15)
        grids_resp.raise_for_status()
        grids = grids_resp.json().get("data", [])
        return [g["url"] for g in grids[:limit]]
    except httpx.HTTPError as e:
        log.warning(f"SteamGridDB search for {name!r} failed: {e}")
        return []
