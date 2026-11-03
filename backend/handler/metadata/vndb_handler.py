"""VNDB, the visual novel database, through its public API (Kana, no key). Its limit is 200 requests in 5 minutes: a game it
has nothing for is not asked again for a week (see scrape_handler's misses), which keeps a library well under it."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from handler.database import db_settings_handler
from handler.metadata import record, web

KEY = "vndb"
SEARCH_RESULTS_ARE_FULL = True  # the query already asks for every field
_API = "https://api.vndb.org/kana/vn"
_FIELDS = "id, title, alttitle, aliases, titles.title, titles.lang, titles.official, description, released, image.url, screenshots.url, developers.name"
_ID = re.compile(r"^v\d+$")
_BBCODE = re.compile(r"\[/?[a-z]+(?:=[^\]]*)?\]")


def enabled_in(settings) -> bool:
    return settings.vndb_enabled is not False


def is_configured() -> bool:
    return enabled_in(db_settings_handler.get_settings())


def _query(filters: list[Any]) -> list[dict[str, Any]]:
    resp = web.request("POST", _API, json={"filters": filters, "fields": _FIELDS, "results": 10})
    if resp is None:
        return []
    try:
        return resp.json().get("results") or []
    except ValueError:
        return []


def _released(text: str | None) -> int | None:
    """VNDB dates are YYYY, YYYY-MM or YYYY-MM-DD; the release is read as the first day of that span."""
    for shape in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return int(datetime.strptime(text or "", shape).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    return None


def _names(item: dict[str, Any]) -> tuple[str, list[str]]:
    """The name to show (the official English title when there is one, else VNDB's own) and every other title and alias the
    novel has, so a name in any language or romanisation can be matched."""
    english = next((t["title"] for t in item.get("titles") or [] if t.get("lang") == "en" and t.get("official")), None)
    shown = english or item.get("title") or ""
    others = [item.get("title"), item.get("alttitle"), *(t.get("title") for t in item.get("titles") or []), *(item.get("aliases") or [])]
    return shown, [name for name in others if name]


def _record(item: dict[str, Any]) -> dict[str, Any]:
    summary = _BBCODE.sub("", item.get("description") or "").strip() or None
    shown, aliases = _names(item)
    return record.make(
        item["id"],
        shown,
        aliases=aliases,
        url=f"https://vndb.org/{item['id']}",
        summary=summary,
        cover=(item.get("image") or {}).get("url"),
        screenshots=[shot.get("url") for shot in item.get("screenshots") or []],
        developers=[dev.get("name") for dev in item.get("developers") or []],
        first_release_date=_released(item.get("released")),
    )


_NOVEL_ADDRESS = re.compile(r"^https?://vndb\.org/(v\d+)", re.IGNORECASE)


def ref_from_input(text: str) -> str | None:
    """The novel a VNDB address (https://vndb.org/v17) or an id (v17) typed by a person points to."""
    text = text.strip()
    found = _NOVEL_ADDRESS.match(text)
    return found.group(1).lower() if found else (text.lower() if _ID.match(text.lower()) else None)


def search(name: str) -> list[dict[str, Any]]:
    return [_record(item) for item in _query(["search", "=", name]) if item.get("id")]


def get(ref: str) -> dict[str, Any] | None:
    if not _ID.match(str(ref)):
        return None
    found = _query(["id", "=", str(ref)])
    return _record(found[0]) if found else None
