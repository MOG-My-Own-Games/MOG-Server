"""itch.io by reading its public pages (no key): the search page's results, then each game's own page. Only pages on
itch.io are read; a game page that asks for an age check gives nothing and is not guessed past. itch.io's search leaves out
adult games for anyone not signed in (a setting of the account), so those are never found by name: a person pastes the
game's page address (or its id) in the match box and gets that game: see ref_from_input."""

from __future__ import annotations

import re
from typing import Any

from handler.database import db_settings_handler
from handler.metadata import record, web

KEY = "itch"
_SEARCH = "https://itch.io/search"
_RESULT = re.compile(r'class="game_title"><a[^>]*href="(https://[^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_GAME = re.compile(r"^([a-z0-9-]+)\.([a-z0-9_-]+)$")  # a game's id: its developer's subdomain and its slug, as "ill.portal-panic"
_SEARCH_RESULTS = 10


def enabled_in(settings) -> bool:
    return settings.itch_enabled is not False


def is_configured() -> bool:
    return enabled_in(db_settings_handler.get_settings())


def _name_and_developer(title: str) -> tuple[str, list[str]]:
    """itch titles read "Portal Panic by ill omens": the name, and the developer after "by"."""
    name, sep, developer = title.rpartition(" by ")
    return (name, [developer]) if sep else (title, [])


def _id_of(url: str) -> str | None:
    """The id of an itch game page address (ill.portal-panic for https://ill.itch.io/portal-panic), or None for any other."""
    found = re.match(r"^https://([a-z0-9-]+)\.itch\.io/([a-z0-9_-]+)$", url)
    return f"{found.group(1)}.{found.group(2)}" if found else None


def ref_from_input(text: str) -> str | None:
    """The game a page address (https://dev.itch.io/game) or an id (dev.game) typed by a person points to."""
    text = text.strip().rstrip("/")
    return _id_of(text) or (text if _GAME.match(text) else None)


def search(name: str) -> list[dict[str, Any]]:
    resp = web.request("GET", _SEARCH, params={"q": name})
    if resp is None:
        return []
    found = []
    for url, title in _RESULT.findall(resp.text)[:_SEARCH_RESULTS]:
        game_id = _id_of(url)
        if game_id:
            found.append(record.make(game_id, web.text_of(title), url=url))
    return found


def get(ref: str) -> dict[str, Any] | None:
    found = _GAME.match(str(ref))
    if not found:
        return None
    url = f"https://{found.group(1)}.itch.io/{found.group(2)}"
    resp = web.request("GET", url)
    if resp is None:
        return None
    page = resp.text
    tags = web.meta_tags(page)
    title = re.search(r"<title>(.*?)</title>", page, re.DOTALL)
    if not title:
        return None
    name, developers = _name_and_developer(web.text_of(title.group(1)))
    return record.make(
        str(ref),
        name,
        url=url,
        summary=tags.get("og:description") or tags.get("description"),
        cover=tags.get("og:image"),
        developers=developers,
    )
