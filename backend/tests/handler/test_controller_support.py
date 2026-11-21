"""The gamepad support Steam declares for a game, looked up by its Steam app (matched, or the one IGDB lists it as)."""

from unittest.mock import patch

import pytest

from handler import scrape_handler
from handler.metadata import igdb_handler, steam_handler, web
from models.game import Game


def _game(**overrides) -> Game:
    defaults = dict(id=1, library_id=1, fs_name="G", name="G", igdb_id=5, provider_ids={"igdb": 5}, igdb_metadata={}, controller_support=None)
    return Game(**{**defaults, **overrides})


@pytest.fixture(autouse=True)
def _steam_on(monkeypatch):
    monkeypatch.setattr(steam_handler, "is_configured", lambda: True)
    monkeypatch.setattr(igdb_handler, "is_configured", lambda: True)


def _run(game, answer, app=3240220, **kw):
    misses: dict = {}
    with patch.object(scrape_handler, "db_game_handler") as db, patch.object(igdb_handler, "steam_app_ids", return_value=[app] if app else []), patch.object(
        steam_handler, "controller_support", side_effect=answer if isinstance(answer, BaseException) else None, return_value=answer
    ):
        db.get_game.return_value = game
        changed = scrape_handler._fill_controller(game, misses, **kw)
    return changed, misses, db


def test_the_value_steam_gives_is_kept_on_the_game():
    changed, misses, db = _run(_game(), "none")
    assert changed and misses == {}
    db.update_game.assert_called_once_with(1, {"controller_support": "none"})


def test_the_steam_app_the_game_is_matched_with_wins_over_igdbs():
    game = _game(provider_ids={"igdb": 5, "steam": 220})
    with patch.object(scrape_handler, "db_game_handler") as db, patch.object(igdb_handler, "steam_app_ids", side_effect=AssertionError), patch.object(
        steam_handler, "controller_support", return_value="full"
    ) as asked:
        db.get_game.return_value = game
        assert scrape_handler._fill_controller(game, {})
    asked.assert_called_once_with(220)


def test_a_game_not_on_steam_is_a_miss_and_is_not_asked_again_for_a_while():
    changed, misses, db = _run(_game(), None, app=None)
    assert not changed and "controller" in misses
    db.update_game.assert_not_called()
    assert not scrape_handler._controller_pending(_game(provider_misses=misses))


def test_steam_being_unreachable_is_no_miss():
    changed, misses, _ = _run(_game(), web.Unavailable("down"))
    assert not changed and misses == {}


def test_a_game_that_has_the_value_is_not_asked_unless_the_scrape_was_asked_for():
    game = _game(controller_support="full")
    assert not scrape_handler._controller_pending(game)
    changed, _, db = _run(game, "partial", refresh=True)
    assert changed
    db.update_game.assert_called_once_with(1, {"controller_support": "partial"})


def test_a_game_with_no_match_waits_for_one():
    assert not scrape_handler._controller_pending(_game(igdb_id=None, provider_ids={}))
    assert scrape_handler._controller_pending(_game())


def test_a_game_that_is_several_steam_apps_takes_the_best_support_of_them():
    """Gotham Knights is listed as two apps (a build with no pad support, and the game with full support): the first one listed
    made it look keyboard-only."""
    game = _game()
    answers = {1877400: "none", 1496790: "full"}
    with patch.object(scrape_handler, "db_game_handler") as db, patch.object(igdb_handler, "steam_app_ids", return_value=[1877400, 1496790]), patch.object(
        steam_handler, "controller_support", side_effect=answers.__getitem__
    ):
        db.get_game.return_value = game
        assert scrape_handler._fill_controller(game, {})
    db.update_game.assert_called_once_with(1, {"controller_support": "full"})


def test_an_app_the_store_would_not_answer_for_does_not_hide_the_one_it_did():
    game = _game()
    answers = {1: None, 2: "partial"}
    with patch.object(scrape_handler, "db_game_handler") as db, patch.object(igdb_handler, "steam_app_ids", return_value=[1, 2]), patch.object(
        steam_handler, "controller_support", side_effect=answers.__getitem__
    ):
        db.get_game.return_value = game
        scrape_handler._fill_controller(game, {})
    db.update_game.assert_called_once_with(1, {"controller_support": "partial"})


def test_with_the_switch_off_nothing_is_asked_of_steam_and_the_game_is_not_waiting_for_it(monkeypatch):
    monkeypatch.setattr(scrape_handler.controller_support, "is_enabled", lambda: False)
    game = _game()
    assert not scrape_handler._controller_pending(game)
    with patch.object(scrape_handler, "db_game_handler"), patch.object(steam_handler, "controller_support", side_effect=AssertionError):
        assert not scrape_handler._fill_controller(game, {}, refresh=True)  # not even a scrape asked for
