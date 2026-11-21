"""The Steam store, through its public storefront API (no key). Titles Steam only shows after its age check are not
returned: no age is declared on anyone's behalf, so such a game is left to the other providers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from handler.database import db_settings_handler
from handler.metadata import record, web

KEY = "steam"
_SEARCH = "https://store.steampowered.com/api/storesearch/"
_DETAILS = "https://store.steampowered.com/api/appdetails"
_STORE = "https://store.steampowered.com/app"
_DATE_SHAPES = ("%d %b, %Y", "%b %d, %Y", "%d %b %Y", "%b %Y", "%Y")


def enabled_in(settings) -> bool:
    return settings.steam_enabled is not False


def is_configured() -> bool:
    return enabled_in(db_settings_handler.get_settings())


def _released(text: str | None) -> int | None:
    """The store's own date text ("21 Oct, 2008", "Oct 2008", "Coming soon" ...) as the first day of its span."""
    for shape in _DATE_SHAPES:
        try:
            return int(datetime.strptime((text or "").strip(), shape).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    return None


_APP_ADDRESS = re.compile(r"^https?://store\.steampowered\.com/app/(\d+)", re.IGNORECASE)


def ref_from_input(text: str) -> str | None:
    """The app a store address (https://store.steampowered.com/app/220/...) or an app id typed by a person points to."""
    text = text.strip()
    found = _APP_ADDRESS.match(text)
    return found.group(1) if found else (text if text.isdigit() else None)


def search(name: str) -> list[dict[str, Any]]:
    resp = web.request("GET", _SEARCH, params={"term": name, "l": "english", "cc": "US"})
    if resp is None:
        return []
    try:
        items = resp.json().get("items") or []
    except ValueError:
        return []
    return [record.make(item["id"], item.get("name") or "", url=f"{_STORE}/{item['id']}") for item in items if item.get("id")]


def _details(ref: str | int) -> dict[str, Any] | None:
    """The store's data for one app, None when it is not a game, is not shown without an age check, or could not be read."""
    if not str(ref).isdigit():
        return None
    resp = web.request("GET", _DETAILS, params={"appids": str(ref), "l": "english", "cc": "US"})
    if resp is None:
        return None
    try:
        answer = resp.json().get(str(ref)) or {}
    except (ValueError, AttributeError):
        return None
    game = answer.get("data") if answer.get("success") else None
    return game if game and game.get("type") == "game" else None


def _controllers(game: dict[str, Any]) -> str:
    """What the store page says about gamepads: `full`, `partial` or `none`. Steam lists controller support only when the developer
    declares it, so a page without it is a game made for the keyboard and the mouse."""
    declared = str(game.get("controller_support") or "").lower()
    if declared in ("full", "partial"):
        return declared
    categories = " ".join(str(c.get("description") or "").lower() for c in game.get("categories") or [])
    if "full controller support" in categories:
        return "full"
    return "partial" if "partial controller support" in categories else "none"


def controller_support(ref: str | int) -> str | None:
    """`full`, `partial` or `none` for the game with this app id; None when the store did not say (see `_details`)."""
    game = _details(ref)
    return _controllers(game) if game else None


def get(ref: str | int) -> dict[str, Any] | None:
    game = _details(ref)
    if not game:
        return None
    return record.make(
        int(ref),
        game.get("name") or "",
        url=f"{_STORE}/{ref}",
        summary=game.get("short_description"),
        cover=game.get("header_image"),
        screenshots=[shot.get("path_full") for shot in game.get("screenshots") or []][:10],
        genres=[genre.get("description") for genre in game.get("genres") or []],
        developers=game.get("developers") or [],
        first_release_date=_released((game.get("release_date") or {}).get("date")),
    )
