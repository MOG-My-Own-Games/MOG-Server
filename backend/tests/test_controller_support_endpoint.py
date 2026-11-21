"""The switch for gamepad support (Settings > Controller support): off, the games go out with no word on gamepads and nothing is
looked up for them."""

import pytest
from endpoints import games as games_endpoints
from endpoints import settings as settings_endpoints
from fastapi import FastAPI
from fastapi.testclient import TestClient
from handler import controller_support
from handler.auth import get_current_user, require_admin
from handler.database import db_game_handler, db_library_handler, db_user_handler
from models.game import Game
from models.library import Library
from models.user import Role, User


@pytest.fixture
def world(db, tmp_path):
    app = FastAPI()
    for module in (games_endpoints, settings_endpoints):
        app.include_router(module.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    game = db_game_handler.add_game(Game(library_id=lib.id, fs_name="Kenshi", name="Kenshi", controller_support="none"))
    return TestClient(app), game


def test_the_switch_is_on_unless_it_was_turned_off(world):
    client, _ = world
    assert client.get("/api/settings").json()["controller_support_enabled"] is True
    assert controller_support.is_enabled()


def test_with_the_switch_off_no_game_goes_out_with_gamepad_support_and_what_was_kept_stays(world):
    client, game = world
    assert client.get("/api/games").json()[0]["controller_support"] == "none"
    assert client.put("/api/settings", json={"controller_support_enabled": False}).json()["controller_support_enabled"] is False
    assert client.get("/api/games").json()[0]["controller_support"] is None
    assert client.get(f"/api/games/{game.id}").json()["controller_support"] is None
    assert db_game_handler.get_game(game.id).controller_support == "none"  # not lost
    client.put("/api/settings", json={"controller_support_enabled": True})
    assert client.get(f"/api/games/{game.id}").json()["controller_support"] == "none"


def test_a_person_can_say_by_hand_whether_a_game_plays_with_a_gamepad_and_it_wins_over_steam(world):
    client, game = world  # Steam says "none"
    body = client.get(f"/api/games/{game.id}").json()
    assert (body["controller_support"], body["controller_support_manual"], body["controller_support_detected"]) == ("none", None, "none")

    done = client.put(f"/api/games/{game.id}", json={"controller_support_manual": "full"}).json()
    assert (done["controller_support"], done["controller_support_manual"], done["controller_support_detected"]) == ("full", "full", "none")
    assert client.get("/api/games").json()[0]["controller_support"] == "full"

    cleared = client.put(f"/api/games/{game.id}", json={"controller_support_manual": None}).json()  # back to what Steam says
    assert (cleared["controller_support"], cleared["controller_support_manual"]) == ("none", None)


def test_an_edit_that_does_not_mention_it_leaves_it_and_a_wrong_value_is_refused(world):
    client, game = world
    client.put(f"/api/games/{game.id}", json={"controller_support_manual": "partial"})
    assert client.put(f"/api/games/{game.id}", json={"summary": "x"}).json()["controller_support_manual"] == "partial"
    assert client.put(f"/api/games/{game.id}", json={"controller_support_manual": "yes"}).status_code == 422


def test_a_scrape_never_changes_what_a_person_said_and_does_not_ask_steam_for_that_game(world):
    from unittest.mock import patch

    from handler import scrape_handler
    from handler.metadata import steam_handler

    client, game = world
    client.put(f"/api/games/{game.id}", json={"controller_support_manual": "full"})
    fresh = db_game_handler.get_game(game.id)
    assert not scrape_handler._controller_pending(fresh)
    with patch.object(steam_handler, "controller_support", side_effect=AssertionError):
        assert not scrape_handler._fill_controller(fresh, {}, refresh=True)
    assert db_game_handler.get_game(game.id).controller_support_manual == "full"


def test_with_the_switch_off_what_a_person_said_is_hidden_too_and_kept(world):
    client, game = world
    client.put(f"/api/games/{game.id}", json={"controller_support_manual": "partial"})
    client.put("/api/settings", json={"controller_support_enabled": False})
    shown = client.get(f"/api/games/{game.id}").json()
    assert (shown["controller_support"], shown["controller_support_manual"], shown["controller_support_detected"]) == (None, None, None)
    assert db_game_handler.get_game(game.id).controller_support_manual == "partial"


def test_the_versions_of_a_title_share_what_a_person_said(world):
    client, game = world
    other = db_game_handler.add_game(Game(library_id=game.library_id, fs_name="Kenshi 2", name="Kenshi", igdb_id=77, igdb_metadata={}))
    db_game_handler.update_game(game.id, {"igdb_id": 77})
    client.put(f"/api/games/{game.id}", json={"controller_support_manual": "full"})
    assert db_game_handler.get_game(other.id).controller_support_manual == "full"
