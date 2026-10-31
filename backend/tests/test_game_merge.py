"""Merge... in Edit metadata: two games put together by hand. Nothing changes until the POST; a game without a folder goes into the
other with all it had; two games with files stay as versions of one title."""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from endpoints import devices as devices_endpoints
from endpoints import games as games_endpoints
from endpoints import saves as saves_endpoints
from fastapi import FastAPI
from fastapi.testclient import TestClient
from handler import media_uploads, saves, titles
from handler import merge as merge_handler
from handler.auth import get_current_user, require_admin
from handler.database import (
    db_game_handler,
    db_launch_settings_handler,
    db_library_handler,
    db_playtime_handler,
    db_saves_handler,
    db_user_handler,
)
from models.game import Game
from models.library import Library
from models.user import Role, User
from PIL import Image

START = datetime(2026, 10, 20, 18, 30, 0, tzinfo=timezone.utc)


def _zip(content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("users/USER/Saved Games/a.sav", content)
    return buf.getvalue()


def _png(color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    monkeypatch.setattr(media_uploads, "RESOURCES_BASE_PATH", str(tmp_path / "resources"))
    monkeypatch.setattr(merge_handler, "ADDONS_PATH", str(tmp_path / "addons"))
    monkeypatch.setattr(games_endpoints, "refresh_game", lambda *a, **k: True)
    app = FastAPI()
    for module in (devices_endpoints, games_endpoints, saves_endpoints):
        app.include_router(module.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    kid = db_user_handler.add_user(User(username="kid", hashed_password="x", role=Role.USER))
    who = {"user": admin}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    app.dependency_overrides[require_admin] = lambda: who["user"]
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    client = TestClient(app)
    device = client.post("/api/devices/register", json={"client_uid": "uid-aaaaaaaa", "hostname": "karasu"}).json()
    return SimpleNamespace(client=client, lib=lib, who=who, admin=admin, kid=kid, device=device, tmp=tmp_path)


def _placeholder(world, name="Pocket Campfire"):
    """A game a client imported: no folder, with the saves and time of the machine that told the server."""
    return db_game_handler.add_game(Game(library_id=None, fs_name=name, name=name, imported=True, missing_from_fs=True))


def _scanned(world, name="PocketCampfire_v2_WindowsInstaller.exe", **extra):
    return db_game_handler.add_game(Game(library_id=world.lib.id, fs_name=name, name=name, size_bytes=450, **extra))


def _save(world, game, content=b"one"):
    return world.client.post(
        f"/api/games/{game.id}/saves",
        params={"device_id": world.device["id"], "trigger": "quit"},
        files={"file": ("save.zip", _zip(content), "application/zip")},
    ).json()["version"]


def _merge(world, this, other, **body):
    return world.client.post(f"/api/games/{this.id}/merge", json={"other_id": other.id, **body})


def test_the_preview_changes_nothing_and_says_what_would_happen(world):
    placeholder, scanned = _placeholder(world), _scanned(world)
    _save(world, placeholder)
    db_playtime_handler.add(world.admin.id, placeholder.id, START, 3600)

    preview = world.client.get(f"/api/games/{scanned.id}/merge/{placeholder.id}")

    assert preview.status_code == 200
    body = preview.json()
    assert (body["mode"], body["conflict"]) == ("merge", None)
    assert body["counts"]["other"]["save_versions"] == 1 and body["counts"]["other"]["playtime_seconds"] == 3600
    assert body["counts"]["this"]["save_versions"] == 0
    assert db_game_handler.get_game(placeholder.id) is not None and len(db_saves_handler.get_for_game_of_all_users(placeholder.id)) == 1


def test_a_game_cannot_be_merged_with_itself_nor_by_a_plain_user(world):
    game = _scanned(world)

    assert _merge(world, game, game).status_code == 400
    assert world.client.get(f"/api/games/{game.id}/merge/9999").status_code == 404


def test_merging_pocket_campfire_brings_the_saves_the_time_and_the_settings_to_the_game_with_the_installer(world):
    """The client's entry (saves, time, settings) and the scan's (installer) were two games the server never put together."""
    placeholder, scanned = _placeholder(world), _scanned(world)
    version = _save(world, placeholder)
    db_playtime_handler.add(world.admin.id, placeholder.id, START, 3600)
    db_launch_settings_handler.save(placeholder.id, None, {"proton": "GE-Proton9"}, None)
    old_file = Path(saves.resolve_path(db_saves_handler.get_version(version["id"]).file_path))
    assert old_file.is_file()

    response = _merge(world, scanned, placeholder, name="Pocket Campfire")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["name"], body["imported"]) == (scanned.id, "Pocket Campfire", False)
    assert db_game_handler.get_game(placeholder.id) is None
    assert merge_handler.merged_into[placeholder.id] == scanned.id
    moved = db_saves_handler.get_version(version["id"])
    assert moved.game_id == scanned.id and f"/{scanned.id}/" in moved.file_path
    assert saves.resolve_path(moved.file_path).is_file() and not old_file.exists()
    assert db_playtime_handler.summary_for_game(world.admin.id, scanned.id) == (3600, 1)
    assert db_launch_settings_handler.list_for_game(scanned.id)[0].proton == "GE-Proton9"
    assert body["playtime_seconds"] == 3600


def test_the_game_the_merge_started_from_keeps_its_folder_and_takes_the_others_when_it_has_none(world):
    placeholder, scanned = _placeholder(world), _scanned(world)

    # started from the placeholder: it stays and takes the scanned game's folder
    merged = _merge(world, placeholder, scanned).json()

    assert merged["id"] == placeholder.id and merged["library_id"] == world.lib.id
    assert (merged["fs_name"], merged["size_bytes"], merged["missing_from_fs"]) == (
        "PocketCampfire_v2_WindowsInstaller.exe", 450, False,
    )


def test_the_time_played_can_be_added_up_or_taken_from_one_side(world):
    results = {}
    for choice in ("sum", "this", "other"):
        this, other = _scanned(world, f"G-{choice}"), _placeholder(world, f"Gp-{choice}")
        db_playtime_handler.add(world.admin.id, this.id, START, 100)
        db_playtime_handler.add(world.admin.id, other.id, START.replace(day=21), 20)
        _merge(world, this, other, playtime=choice)
        results[choice] = db_playtime_handler.summary_for_game(world.admin.id, this.id)[0]

    assert results == {"sum": 120, "this": 100, "other": 20}


def test_a_session_both_games_were_told_counts_once(world):
    this, other = _scanned(world), _placeholder(world)
    db_playtime_handler.add(world.admin.id, this.id, START, 100)
    db_playtime_handler.add(world.admin.id, other.id, START, 100)

    _merge(world, this, other)

    assert db_playtime_handler.summary_for_game(world.admin.id, this.id) == (100, 1)


def test_launch_settings_both_have_for_a_device_keep_the_started_from_games(world):
    this, other = _scanned(world), _placeholder(world)
    db_launch_settings_handler.save(this.id, None, {"proton": "mine"}, None)
    db_launch_settings_handler.save(other.id, None, {"proton": "theirs"}, None)

    _merge(world, this, other)

    rows = db_launch_settings_handler.list_for_game(this.id)
    assert [r.proton for r in rows] == ["mine"]


def test_the_name_and_summary_are_what_the_person_typed_and_the_igdb_id_follows_the_choice(world):
    this = _scanned(world, igdb_id=None, summary="old")
    other = _placeholder(world)
    db_game_handler.update_game(other.id, {"igdb_id": 55, "igdb_metadata": {"name": "PC"}})

    body = _merge(world, this, other, name="Typed by hand", summary="New text", igdb_id="other").json()

    assert (body["name"], body["summary"], body["igdb_id"]) == ("Typed by hand", "New text", 55)
    assert db_game_handler.get_game(this.id).igdb_metadata == {"name": "PC"}


def test_the_artwork_chosen_from_the_other_game_is_copied_under_the_kept_id(world):
    this, other = _scanned(world), _placeholder(world)
    url = media_uploads.store(other.id, "cover", _png())
    db_game_handler.update_game(other.id, {"media": {"cover": {"url": url, "source": "upload"}}, "cover_path": url})
    mine = media_uploads.store(this.id, "banner", _png((1, 2, 3)))
    db_game_handler.update_game(this.id, {"media": {"banner": {"url": mine, "source": "upload"}}})

    body = _merge(world, this, other, media={"cover": "other", "banner": "none"}).json()

    new_url = body["media"]["cover"]["url"]
    assert new_url.startswith(f"/resources/media/{this.id}/cover-") and body["cover_path"] == new_url
    assert media_uploads.path_for(new_url).is_file()
    assert "banner" not in body["media"] and not media_uploads.path_for(mine).exists()  # replaced: its file goes
    assert not (media_uploads.media_dir() / str(other.id)).exists()


def test_two_games_that_both_have_files_are_versions_not_a_merge(world):
    one, two = _scanned(world, "A"), _scanned(world, "B")

    preview = world.client.get(f"/api/games/{one.id}/merge/{two.id}").json()

    assert preview["mode"] == "link"


def test_linking_versions_gives_them_the_chosen_igdb_id_and_keeps_both_games(world):
    one, two = _scanned(world, "A", igdb_id=None), _scanned(world, "B")
    db_game_handler.update_game(two.id, {"igdb_id": 77})
    _save(world, one)

    response = _merge(world, one, two, igdb_id="other", name="The Game")

    assert response.status_code == 200
    a, b = db_game_handler.get_game(one.id), db_game_handler.get_game(two.id)
    assert (a.igdb_id, b.igdb_id, a.name, b.name) == (77, 77, "The Game", "The Game")
    assert titles.title_key(a) == titles.title_key(b)
    assert world.client.get(f"/api/games/{two.id}/saves").json()["devices"][0]["versions"][0]["game_id"] == one.id


def test_linking_versions_nobody_matched_ties_them_with_a_title_of_our_own(world):
    one, two = _scanned(world, "A"), _scanned(world, "B")

    _merge(world, one, two)

    a, b = db_game_handler.get_game(one.id), db_game_handler.get_game(two.id)
    assert a.title_key == b.title_key == f"mog:{one.id}" and a.igdb_id is None and b.igdb_id is None
    assert titles.title_key(a) == titles.title_key(b)


def test_an_install_in_progress_stops_the_merge_and_nothing_changes(world):
    from models.install_session import InstallSession, InstallSessionState

    this, other = _scanned(world), _placeholder(world)
    _save(world, other)
    from decorators.database import begin_session

    @begin_session
    def add(session=None):
        session.add(InstallSession(game_id=other.id, user_id=world.admin.id, state=InstallSessionState.INSTALLING))

    add()

    response = _merge(world, this, other)

    assert response.status_code == 409 and response.json()["detail"]["code"] == "install_in_progress"
    assert db_game_handler.get_game(other.id) is not None
    assert len(db_saves_handler.get_for_game_of_all_users(other.id)) == 1


def test_a_failure_halfway_puts_the_files_back(world, monkeypatch):
    this, other = _scanned(world), _placeholder(world)
    version = _save(world, other)
    path = saves.resolve_path(db_saves_handler.get_version(version["id"]).file_path)
    monkeypatch.setattr(db_game_handler, "merge_games", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db is down")))

    with pytest.raises(RuntimeError):
        merge_handler.merge(this, other, {}, db_game_handler)

    assert path.is_file()
    assert db_saves_handler.get_version(version["id"]).game_id == other.id


def test_a_plain_user_cannot_merge(world, monkeypatch):
    from fastapi import HTTPException

    def refuse():
        raise HTTPException(status_code=403)

    world.client.app.dependency_overrides[require_admin] = refuse
    this, other = _scanned(world), _placeholder(world)

    assert _merge(world, this, other).status_code == 403
    assert db_game_handler.get_game(other.id) is not None
