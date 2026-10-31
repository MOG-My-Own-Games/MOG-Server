"""Versions of one game: the games with the same title share the time played, the saves a client is offered, the name and the
artwork; nothing is moved, so telling the server the IGDB match was wrong splits them again."""

import io
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from endpoints import devices as devices_endpoints
from endpoints import games as games_endpoints
from endpoints import playtime as playtime_endpoints
from endpoints import saves as saves_endpoints
from fastapi import FastAPI
from fastapi.testclient import TestClient
from handler import saves, titles
from handler.auth import get_current_user
from handler.database import db_game_handler, db_library_handler, db_user_handler
from models.game import Game
from models.library import Library
from models.user import Role, User

START = datetime(2026, 10, 20, 18, 30, 0, tzinfo=timezone.utc)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    app = FastAPI()
    for module in (devices_endpoints, games_endpoints, playtime_endpoints, saves_endpoints):
        app.include_router(module.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    who = {"user": admin}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    from handler.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: who["user"]
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    other_lib = db_library_handler.add_library(Library(name="Hidden", root_path=str(tmp_path / "lib2")))
    client = TestClient(app)
    device = client.post("/api/devices/register", json={"client_uid": "uid-aaaaaaaa", "hostname": "karasu"}).json()
    return SimpleNamespace(client=client, lib=lib, other_lib=other_lib, who=who, admin=admin, device=device)


def _game(world, name, igdb_id=None, **extra):
    return db_game_handler.add_game(Game(library_id=world.lib.id, fs_name=name, name=name, igdb_id=igdb_id, **extra))


def _play(world, game, started, seconds):
    return world.client.post(
        f"/api/games/{game.id}/playtime", json={"started_at": started.isoformat(), "seconds": seconds}
    )


def _save(world, game, content=b"one"):
    return world.client.post(
        f"/api/games/{game.id}/saves",
        params={"device_id": world.device["id"], "trigger": "quit"},
        files={"file": ("save.zip", _zip({"users/USER/Saved Games/a.sav": content}), "application/zip")},
    )


def test_a_title_is_told_by_its_title_key_then_the_igdb_id_then_the_game_itself(world):
    matched, same, alone = _game(world, "A", 7), _game(world, "A2", 7), _game(world, "B")
    tied = _game(world, "C", 7, title_key="mog:99")

    assert titles.title_key(matched) == titles.title_key(same) == "igdb:7"
    assert titles.title_key(alone) == f"mog:{alone.id}"
    assert titles.title_key(tied) == "mog:99"  # tied by hand: the IGDB id no longer says
    assert [g.id for g in titles.versions(matched, world.admin)] == [matched.id, same.id]


def test_the_games_of_the_list_say_which_title_they_belong_to(world):
    first, second = _game(world, "A", 7), _game(world, "B", 7)

    keys = {g["id"]: g["title_key"] for g in world.client.get("/api/games").json()}

    assert keys == {first.id: "igdb:7", second.id: "igdb:7"}


def test_the_time_played_is_the_titles_not_a_versions(world):
    old, new = _game(world, "Old", 7), _game(world, "New", 7)
    _play(world, old, START, 3600)
    _play(world, new, START + timedelta(days=1), 600)

    played = {g["id"]: g["playtime_seconds"] for g in world.client.get("/api/games").json()}

    assert played == {old.id: 4200, new.id: 4200}  # every version says the title's total
    assert world.client.get(f"/api/games/{new.id}").json()["playtime_seconds"] == 4200
    assert world.client.get(f"/api/games/{old.id}/playtime").json()["playtime_seconds"] == 4200
    assert _play(world, new, START + timedelta(days=2), 60).json()["playtime_seconds"] == 4260


def test_a_wrong_igdb_match_corrected_gives_the_time_back_to_where_it_was_played(world):
    keeper, keepher = _game(world, "Dungeon Keeper", 7), _game(world, "Dungeon KeepHer", 7)
    _play(world, keeper, START, 3600)
    _play(world, keepher, START + timedelta(days=1), 600)
    assert world.client.get(f"/api/games/{keeper.id}").json()["playtime_seconds"] == 4200

    db_game_handler.update_game(keepher.id, {"igdb_id": 8})  # the match was wrong

    assert world.client.get(f"/api/games/{keeper.id}").json()["playtime_seconds"] == 3600
    assert world.client.get(f"/api/games/{keepher.id}").json()["playtime_seconds"] == 600


def test_the_saves_of_every_version_are_listed_with_the_version_that_made_them(world):
    old, new = _game(world, "Old", 7), _game(world, "New", 7)
    first = _save(world, old, b"old").json()["version"]
    second = _save(world, new, b"new").json()["version"]

    listing = world.client.get(f"/api/games/{new.id}/saves").json()

    versions = {v["id"]: v for d in listing["devices"] for v in d["versions"]}
    assert set(versions) == {first["id"], second["id"]}
    assert (versions[first["id"]]["game_id"], versions[first["id"]]["game_name"]) == (old.id, "Old")
    assert (versions[second["id"]]["game_id"], versions[second["id"]]["fs_name"]) == (new.id, "New")


def test_the_same_files_saved_by_another_version_are_not_kept_twice(world):
    old, new = _game(world, "Old", 7), _game(world, "New", 7)
    _save(world, old, b"same")

    again = _save(world, new, b"same").json()

    assert again["created"] is False and again["version"]["game_id"] == old.id


def test_saves_stay_apart_for_games_that_are_not_the_same_title(world):
    one, two = _game(world, "One", 7), _game(world, "Two", 8)
    _save(world, one)

    assert world.client.get(f"/api/games/{two.id}/saves").json()["devices"] == []


def test_a_version_in_a_library_the_user_may_not_see_does_not_count(world):
    visible = _game(world, "A", 7)
    hidden = db_game_handler.add_game(Game(library_id=world.other_lib.id, fs_name="H", name="H", igdb_id=7))
    plain = db_user_handler.add_user(User(username="kid", hashed_password="x", role=Role.USER))
    plain.hidden_library_ids = [world.other_lib.id]

    assert [g.id for g in titles.versions(visible, plain)] == [visible.id]
    assert [g.id for g in titles.versions(visible, world.admin)] == [visible.id, hidden.id]


def test_the_name_and_summary_changed_on_a_version_are_the_titles(world):
    one, two = _game(world, "One", 7), _game(world, "Two", 7)

    world.client.put(f"/api/games/{one.id}", json={"summary": "A good one"})

    assert db_game_handler.get_game(two.id).summary == "A good one"


def test_matching_a_version_by_hand_to_another_igdb_id_takes_it_out_of_the_title(world, monkeypatch):
    monkeypatch.setattr(games_endpoints, "refresh_game", lambda *a, **k: True)
    one, two = _game(world, "One", 7), _game(world, "Two", 7, title_key="mog:99")

    world.client.put(f"/api/games/{two.id}", json={"igdb_id": 8})

    assert db_game_handler.get_game(two.id).title_key is None  # it follows its IGDB id again
    assert titles.title_key(db_game_handler.get_game(two.id)) == "igdb:8"
    assert titles.title_key(one) == "igdb:7"


def test_splitting_a_version_off_gives_each_game_back_its_own_time_and_saves(world):
    keeper, keepher = _game(world, "Dungeon Keeper", 7), _game(world, "Dungeon KeepHer", 7)
    _play(world, keeper, START, 3600)
    _play(world, keepher, START + timedelta(days=1), 600)
    _save(world, keepher)

    split = world.client.post(f"/api/games/{keepher.id}/split")

    assert split.status_code == 200 and split.json()["title_key"] == f"mog:{keepher.id}"
    assert world.client.get(f"/api/games/{keeper.id}").json()["playtime_seconds"] == 3600
    assert world.client.get(f"/api/games/{keepher.id}").json()["playtime_seconds"] == 600
    assert world.client.get(f"/api/games/{keeper.id}/saves").json()["devices"] == []
    assert len(world.client.get(f"/api/games/{keepher.id}/saves").json()["devices"]) == 1
