"""RomM as a metadata source: the games on the RomM server the user runs (any RomM, at the address given in Settings), which
has already matched them with its own providers. Follows RomM 5.4's API: a client API token (`rmm_...`, made in RomM's
profile or got by exchanging a pairing code) sent as a bearer token to GET /api/roms."""

from __future__ import annotations

import re
from typing import Any

from handler.database import db_settings_handler
from handler.metadata import record, web

KEY = "romm"
_TOKEN = re.compile(r"^rmm_[0-9a-f]{64}$")
_CODE = re.compile(r"^\d{8}$")


def enabled_in(settings) -> bool:
    return settings.romm_enabled is not False


def complete(settings) -> bool:
    """Whether an address and a token are both given, and the address is web (http or https)."""
    return bool(settings.romm_url and settings.romm_token and settings.romm_url.startswith(("http://", "https://")))


def _config(settings=None) -> tuple[str, str] | None:
    """Address and token of the server, or None when it is switched off or not complete."""
    settings = settings or db_settings_handler.get_settings()
    if not enabled_in(settings) or not complete(settings):
        return None
    return settings.romm_url.rstrip("/"), settings.romm_token


def is_configured() -> bool:
    return _config() is not None


def kind_of(value: str) -> str | None:
    """What a typed value is: a `token` to keep as it is, a pairing `code` to exchange, or None (neither)."""
    value = value.strip()
    if _TOKEN.match(value):
        return "token"
    return "code" if _CODE.match(value.replace("-", "")) else None


def exchange(base: str, code: str) -> str | None:
    """The API token RomM gives for a pairing code (made in RomM by the token's owner, valid 5 minutes, used once), or None when
    the server does not take it."""
    try:
        resp = web.request("POST", f"{base.rstrip('/')}/api/client-tokens/exchange", json={"code": code.replace("-", "").strip()})
    except web.Unavailable:
        return None
    if resp is None:
        return None
    try:
        token = resp.json()["raw_token"]
    except (ValueError, KeyError, TypeError):
        return None
    return token if isinstance(token, str) and _TOKEN.match(token) else None


def _get(base: str, token: str, path: str, params: dict[str, Any] | None = None) -> Any | None:
    resp = web.request("GET", f"{base}/api/{path}", params=params, headers={"Authorization": f"Bearer {token}"})
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _api(path: str, params: dict[str, Any] | None = None) -> Any | None:
    """The JSON answer of one GET under /api."""
    config = _config()
    return _get(*config, path, params) if config else None


def validate(settings) -> bool:
    """Whether the server takes the token given in these settings (Settings' pass/fail badge)."""
    config = _config(settings)
    if config is None:
        return False
    try:
        return _get(*config, "roms", {"limit": 1}) is not None
    except web.Unavailable:
        return False


_ROM_ADDRESS = re.compile(r"^https?://[^/\s]+(?:/[^/\s]+)*?/rom/(\d+)", re.IGNORECASE)


def ref_from_input(text: str) -> str | None:
    """The game a RomM page address (https://romm.example/rom/12) or a game id typed by a person points to."""
    text = text.strip()
    found = _ROM_ADDRESS.match(text)
    return found.group(1) if found else (text if text.isdigit() else None)


def _record(item: dict[str, Any]) -> dict[str, Any]:
    cover = item.get("url_cover")
    return record.make(
        item["id"],
        item.get("name") or "",
        summary=item.get("summary"),
        cover=cover if isinstance(cover, str) and cover.startswith("https://") else None,
        genres=item.get("genres") or [],
        developers=item.get("developers") or [],
        first_release_date=item.get("first_release_date"),
    )


def search(name: str) -> list[dict[str, Any]]:
    body = _api("roms", {"search_term": name, "limit": 10})
    items = body.get("items") if isinstance(body, dict) else None
    return [_record(item) for item in items or [] if isinstance(item, dict) and item.get("id") is not None]


def get(ref: str | int) -> dict[str, Any] | None:
    if not str(ref).isdigit():
        return None
    item = _api(f"roms/{int(ref)}")
    return _record(item) if isinstance(item, dict) and item.get("id") is not None else None
