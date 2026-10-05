import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import games as games_endpoints
from endpoints import users as users_endpoints
from handler import saves
from handler.auth import get_current_user
from handler.database import (
    db_device_handler,
    db_game_handler,
    db_library_handler,
    db_saves_handler,
    db_user_handler,
)
from models.device import Device
from models.game import Game
from models.library import Library
from models.user import Role, User


def _zip(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("game/a.sav", content)
    return path


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    app = FastAPI()
    app.include_router(users_endpoints.router, prefix="/api")
    app.include_router(games_endpoints.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    app.dependency_overrides[get_current_user] = lambda: admin
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    return SimpleNamespace(client=TestClient(app), admin=admin, lib=lib, tmp=tmp_path)


def add_user(name: str) -> User:
    return db_user_handler.add_user(User(username=name, hashed_password="x", role=Role.USER))


def add_game(world, name: str, **flags) -> Game:
    return db_game_handler.add_game(Game(library_id=world.lib.id, fs_name=name, name=name, **flags))


def add_save(world, user: User, game: Game, content: bytes = b"1"):
    device = db_device_handler.get_for_user(user.id)
    if not device:
        device = [
            db_device_handler.add_device(
                Device(user_id=user.id, client_uid=f"uid-{user.id}-xxxx", name=f"pc{user.id}")
            )
        ]
    incoming = _zip(world.tmp, f"in-{user.id}-{game.id}.zip", content)
    version, _ = saves.store_version(user.id, game.id, device[0].id, "quit", incoming)
    return version


def saves_dir(world) -> Path:
    return world.tmp / "saves"


def test_a_user_without_saves_is_deleted_with_their_devices(world):
    user = add_user("bob")
    db_device_handler.add_device(Device(user_id=user.id, client_uid="uid-bob-xxxx", name="bobpc"))

    assert world.client.delete(f"/api/users/{user.id}").status_code == 200

    assert db_user_handler.get_user(user.id) is None and db_device_handler.get_for_user(user.id) == []


def test_a_user_with_saves_is_refused_until_the_loss_is_confirmed(world):
    bob, carol = add_user("bob"), add_user("carol")
    game = add_game(world, "G")
    mine = add_save(world, bob, game, b"bob's")
    add_save(world, bob, game, b"bob's again")
    theirs = add_save(world, carol, game, b"carol's")

    refused = world.client.delete(f"/api/users/{bob.id}")

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "has_saves" and detail["versions"] == 2 and detail["size_bytes"] > 0
    assert db_user_handler.get_user(bob.id) is not None and saves.resolve_path(mine.file_path).is_file()

    assert world.client.delete(f"/api/users/{bob.id}", params={"delete_saves": True}).status_code == 200

    assert db_user_handler.get_user(bob.id) is None and db_saves_handler.get_for_game(bob.id, game.id) == []
    assert db_device_handler.get_for_user(bob.id) == []
    assert not saves.resolve_path(mine.file_path).exists() and not (saves_dir(world) / str(bob.id)).exists()
    assert saves.resolve_path(theirs.file_path).is_file()  # another user's saves are untouched
    assert len(db_saves_handler.get_for_game(carol.id, game.id)) == 1


def test_an_admin_cannot_delete_themselves(world):
    assert (
        world.client.delete(f"/api/users/{world.admin.id}", params={"delete_saves": True}).status_code == 400
    )


def test_a_game_gone_from_disk_that_has_saves_is_saves_only_not_missing(world):
    user = add_user("bob")
    gone_with = add_game(world, "WithSaves", missing_from_fs=True)
    gone_without = add_game(world, "Without", missing_from_fs=True)
    present = add_game(world, "Present")
    add_save(world, user, gone_with)
    add_save(world, user, present)

    flags = {
        g["name"]: (g["missing_from_fs"], g["saves_only"]) for g in world.client.get("/api/games").json()
    }
    assert flags == {"WithSaves": (True, True), "Without": (True, False), "Present": (False, False)}
    assert world.client.get(f"/api/games/{gone_with.id}").json()["saves_only"] is True
    assert world.client.get(f"/api/games/{present.id}").json()["saves_only"] is False
    assert world.client.get(f"/api/games/{gone_without.id}").json()["saves_only"] is False
    listed = {g["name"]: g["saves_only"] for g in world.client.get("/api/games/missing").json()}
    assert listed == {"Without": False, "WithSaves": True}


def test_clearing_the_missing_games_keeps_the_ones_with_saves(world):
    user = add_user("bob")
    keep = add_game(world, "WithSaves", missing_from_fs=True)
    add_game(world, "Without", missing_from_fs=True)
    add_save(world, user, keep)

    result = world.client.delete("/api/games/missing").json()

    assert result == {"cleared": 1, "kept_with_saves": 1}
    assert [g.name for g in db_game_handler.get_all_games()] == ["WithSaves"]


def test_deleting_a_saves_only_game_needs_the_saves_to_be_given_up(world):
    bob, carol = add_user("bob"), add_user("carol")
    game = add_game(world, "WithSaves", missing_from_fs=True)
    other = add_game(world, "Other")
    a, b = add_save(world, bob, game, b"a"), add_save(world, carol, game, b"b")
    keep = add_save(world, bob, other, b"keep")

    refused = world.client.delete(f"/api/games/{game.id}")
    assert refused.status_code == 409 and refused.json()["detail"] == {
        "code": "has_saves",
        "versions": 2,
        "size_bytes": refused.json()["detail"]["size_bytes"],
    }
    assert db_game_handler.get_game(game.id) is not None

    assert world.client.delete(f"/api/games/{game.id}", params={"delete_saves": True}).status_code == 200

    assert db_game_handler.get_game(game.id) is None
    assert not saves.resolve_path(a.file_path).exists() and not saves.resolve_path(b.file_path).exists()
    assert saves.resolve_path(keep.file_path).is_file() and db_saves_handler.game_ids_with_saves() == {
        other.id
    }


def test_a_game_without_saves_is_deleted_as_before_and_a_present_one_is_refused(world):
    gone = add_game(world, "Gone", missing_from_fs=True)
    present = add_game(world, "Present")
    assert world.client.delete(f"/api/games/{gone.id}").status_code == 200
    assert world.client.delete(f"/api/games/{present.id}").status_code == 409


def test_the_add_ons_only_flag_reaches_the_api(world):
    add_game(world, "OnlyMods", addons_only=True)
    add_game(world, "Full")
    flags = {g["name"]: g["addons_only"] for g in world.client.get("/api/games").json()}
    assert flags == {"OnlyMods": True, "Full": False}
