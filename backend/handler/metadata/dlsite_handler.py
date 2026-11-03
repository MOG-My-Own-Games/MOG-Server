"""DLsite (the maniax site, doujin and adult games) by reading its product pages: DLsite has no API. A folder that carries a
product code (RJ123456, RE..., VJ..., BJ...) is matched by that code; otherwise the search page's first results are read.

The site asks adults to confirm their age on first visit; the request carries the cookie its own confirmation button sets
(adultchecked=1). Nothing else is sent."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from handler.database import db_settings_handler
from handler.metadata import record, web

KEY = "dlsite"
_SITE = "https://www.dlsite.com/maniax"
_CONFIRM_AGE = {"adultchecked": "1"}
_ENGLISH = {"locale": "en_US"}  # the English title and text of a work, where it has them (else its Japanese ones)
_CODE = re.compile(r"\b(RJ|RE|VJ|BJ)\d{6,8}\b", re.IGNORECASE)
_SEARCH_RESULTS = 8
_RESULT = re.compile(r'product_id/((?:RJ|RE|VJ|BJ)\d{6,8})\.html"\s+title="([^"]*)"')


def enabled_in(settings) -> bool:
    return settings.dlsite_enabled is not False


def is_configured() -> bool:
    return enabled_in(db_settings_handler.get_settings())


def ref_from_name(names: list[str]) -> str | None:
    """The product code a name carries, if any: the match is then exact, with no search."""
    for name in names:
        found = _CODE.search(name)
        if found:
            return found.group(0).upper()
    return None


def ref_from_input(text: str) -> str | None:
    """The work a product address (.../product_id/RJ155385.html) or a code typed by a person points to."""
    return ref_from_name([text])


def get(ref: str) -> dict[str, Any] | None:
    code = str(ref).upper()
    if not _CODE.fullmatch(code):
        return None
    resp = web.request("GET", f"{_SITE}/work/=/product_id/{code}.html", params=_ENGLISH, cookies=_CONFIRM_AGE)
    if resp is None:
        return None
    page = resp.text
    tags = web.meta_tags(page)
    heading = re.search(r'<h1[^>]*id="work_name"[^>]*>(.*?)</h1>', page, re.DOTALL)
    name = web.text_of(heading.group(1)) if heading else (tags.get("og:title") or "").removesuffix(" | DLsite")
    if not name:
        return None
    maker = re.search(r'maker_name.{0,300}?<a[^>]*>(.*?)</a>', page, re.DOTALL)
    return record.make(
        code,
        name,
        url=f"{_SITE}/work/=/product_id/{code}.html",
        summary=tags.get("og:description"),
        cover=tags.get("og:image"),
        developers=[web.text_of(maker.group(1))] if maker else [],
    )


def search(name: str) -> list[dict[str, Any]]:
    """The first results of the search page, each with its code and title; get() reads the whole page of the one chosen."""
    # A space is "+" here: the site refuses (403) a keyword with the "%20" form of it.
    # Games only, in English: without the category the English search finds nothing, and in Japanese a title is not one the
    # name of a folder can be compared with.
    resp = web.request(
        "GET", f"{_SITE}/fsr/=/keyword/{quote_plus(name)}/work_type_category%5B0%5D/game", params=_ENGLISH, cookies=_CONFIRM_AGE
    )
    if resp is None:
        return []
    found: dict[str, str] = {}
    for code, title in _RESULT.findall(resp.text):
        found.setdefault(code.upper(), web.text_of(title))
    return [record.make(code, title, url=f"{_SITE}/work/=/product_id/{code}.html") for code, title in list(found.items())[:_SEARCH_RESULTS]]
