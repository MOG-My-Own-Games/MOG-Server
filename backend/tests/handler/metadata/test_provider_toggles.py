import asyncio
from types import SimpleNamespace

import pytest

import config
from endpoints import settings as settings_endpoint
from handler.metadata import hltb_handler, igdb_handler, sgdb_handler


def row(**over):
    base = dict(
        igdb_client_id="id",
        igdb_client_secret="secret",
        steamgriddb_api_key="key",
        igdb_enabled=None,
        steamgriddb_enabled=None,
        hltb_enabled=None,
        watch_libraries=None,
        install_cache_ttl_days=None,
        install_default_proton_build=None,
        install_default_auto_mode=None,
        install_default_manual_mode=None,
        download_workers=None,
    )
    return SimpleNamespace(**{**base, **over})


@pytest.fixture
def stored(monkeypatch):
    """The settings row every handler reads; a test changes it through the returned dict."""
    current = {"row": row()}
    getter = lambda: current["row"]  # noqa: E731
    for module in (igdb_handler, sgdb_handler, hltb_handler, settings_endpoint):
        monkeypatch.setattr(module.db_settings_handler, "get_settings", getter)
    return current


@pytest.fixture
def no_network(monkeypatch):
    import httpx

    def refuse(*args, **kwargs):
        raise AssertionError("a provider that is switched off must not be asked anything")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(httpx, "get", refuse)


def test_a_switch_never_touched_means_on(stored):
    assert igdb_handler.is_enabled() and sgdb_handler.is_enabled()


def test_igdb_off_has_no_credentials_and_asks_nothing_but_keeps_what_is_saved(stored, no_network):
    stored["row"] = row(igdb_enabled=False)

    assert igdb_handler.is_enabled() is False
    assert igdb_handler._credentials() == (None, None)
    assert igdb_handler.search_games("Doom") == [] and igdb_handler.get_game_by_id(1) is None
    assert stored["row"].igdb_client_id == "id" and stored["row"].igdb_client_secret == "secret"


def test_steamgriddb_off_finds_nothing_and_asks_nothing(stored, no_network):
    stored["row"] = row(steamgriddb_enabled=False)

    assert sgdb_handler._api_key() is None
    assert sgdb_handler.search_games("Doom") == [] and sgdb_handler.get_media(1) == {}
    assert sgdb_handler.get_grids(1) == []


def test_steamgriddb_on_uses_the_saved_key(stored):
    assert sgdb_handler._api_key() == "key"


def test_howlongtobeat_follows_the_environment_until_the_switch_is_used(stored, monkeypatch):
    monkeypatch.setattr(config, "HLTB_ENABLED", True)
    assert hltb_handler.is_enabled() is True
    monkeypatch.setattr(config, "HLTB_ENABLED", False)
    assert hltb_handler.is_enabled() is False

    stored["row"] = row(hltb_enabled=True)
    assert hltb_handler.is_enabled() is True  # the switch wins over the environment
    monkeypatch.setattr(config, "HLTB_ENABLED", True)
    stored["row"] = row(hltb_enabled=False)
    assert hltb_handler.is_enabled() is False
    assert hltb_handler.search_games("Doom") == []


def test_the_settings_answer_says_whether_each_provider_is_on(stored, monkeypatch):
    monkeypatch.setattr(config, "HLTB_ENABLED", True)
    stored["row"] = row(igdb_enabled=False)

    result = asyncio.run(settings_endpoint.get_settings(SimpleNamespace()))

    assert (result.igdb_enabled, result.steamgriddb_enabled, result.hltb_enabled) == (False, True, True)
    assert result.igdb_client_id == "id" and result.igdb_client_secret == "secret"  # still there


def test_a_provider_that_is_off_is_not_validated(stored, monkeypatch):
    checked = []
    monkeypatch.setattr(igdb_handler, "validate_credentials", lambda: checked.append("igdb") or True)
    monkeypatch.setattr(sgdb_handler, "validate_key", lambda: checked.append("sgdb") or True)
    stored["row"] = row(igdb_enabled=False)

    result = asyncio.run(settings_endpoint.validate_settings(SimpleNamespace()))

    assert (result.igdb_valid, result.steamgriddb_valid) == (None, True) and checked == ["sgdb"]
