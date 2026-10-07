# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""The HowLongToBeat search wire contract: headers, payload and session parsing."""

from __future__ import annotations

from typing import Any, Final, NamedTuple

HLTB_BASE_URL: Final[str] = "https://howlongtobeat.com"
# The search route HowLongToBeat serves today; it rotates, so the handler can rediscover it.
HLTB_SEARCH_URL: Final[str] = f"{HLTB_BASE_URL}/api/search/site"

# A session is issued at the search route's own /init sibling.
SESSION_MINT_SUFFIX: Final[str] = "/init"

# The firewall answers tool-style "Name/version" agents with a 403, and the session is bound to the
# agent it was minted with, so every call has to send this one.
HLTB_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class HLTBSession(NamedTuple):
    token: str
    # The honeypot pair is optional and is echoed back only when /init issued it.
    hp_key: str | None = None
    hp_val: str | None = None

    def honeypot(self) -> tuple[str, str] | None:
        if self.hp_key and self.hp_val:
            return self.hp_key, self.hp_val
        return None


def parse_session(data: object) -> HLTBSession | None:
    """The session an /init response issued, or None if it issued none."""
    if not isinstance(data, dict) or not data.get("token"):
        return None
    return HLTBSession(data["token"], data.get("hpKey"), data.get("hpVal"))


def base_headers(base_url: str = HLTB_BASE_URL) -> dict[str, str]:
    return {"Referer": base_url, "User-Agent": HLTB_USER_AGENT}


def search_headers(session: HLTBSession, base_url: str = HLTB_BASE_URL) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        **base_headers(base_url),
        # The site's own search is a same-origin POST, which browsers send with an Origin.
        "Origin": base_url,
        "x-auth-token": session.token,
    }
    if honeypot := session.honeypot():
        headers["x-hp-key"], headers["x-hp-val"] = honeypot
    return headers


def search_body(payload: dict[str, Any], session: HLTBSession) -> dict[str, Any]:
    """The payload with the honeypot pair added. The key rotates with the session, so it goes on a copy."""
    honeypot = session.honeypot()
    return {**payload, honeypot[0]: honeypot[1]} if honeypot else payload


def build_search_payload(search_term: str) -> dict[str, Any]:
    return {
        "searchType": "games",
        "searchTerms": search_term.split(" "),
        "searchPage": 1,
        "size": 20,
        "searchOptions": {
            "games": {
                "userId": 0,
                "platform": "",
                "sortCategory": "popular",
                "rangeCategory": "main",
                "rangeTime": {"min": None, "max": None},
                "gameplay": {"perspective": "", "flow": "", "genre": "", "difficulty": ""},
                "rangeYear": {"min": "", "max": ""},
                "modifier": "",
            },
            "users": {"sortCategory": "postcount"},
            "lists": {"sortCategory": "follows"},
            "filter": "",
            "sort": 0,
            "randomizer": 0,
        },
        "useCache": True,
    }
