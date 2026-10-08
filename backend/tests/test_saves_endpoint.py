import io
import zipfile
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import devices as devices_endpoints
from endpoints import saves as saves_endpoints
from handler import saves
from handler.auth import get_current_user

GAME = SimpleNamespace(id=5, name="Some Game", library_id=1)
UID_A, UID_B = "uid-aaaaaaaa", "uid-bbbbbbbb"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def api(db, monkeypatch, tmp_path):
    """The devices and saves routers on a minimal app, signed in as user 1 (a plain user)."""
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    monkeypatch.setattr(
        saves_endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME if i == 5 else None)
    )
    app = FastAPI()
    app.include_router(devices_endpoints.router, prefix="/api")
    app.include_router(saves_endpoints.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    client.app_ = app
    return client


def _register(api, uid=UID_A, hostname="karasu", **extra):
    return api.post("/api/devices/register", json={"client_uid": uid, "hostname": hostname, **extra})


def _upload(api, device_id, files=None, trigger="quit", game_id=5):
    body = _zip(files or {"users/USER/Saved Games/a.sav": b"one"})
    return api.post(
        f"/api/games/{game_id}/saves",
        params={"device_id": device_id, "trigger": trigger},
        files={"file": ("save.zip", body, "application/zip")},
    )


def test_registering_a_second_machine_with_the_same_hostname_is_a_conflict_that_lists_the_first(api):
    first = _register(api).json()

    clash = _register(api, UID_B, os_id="fedora")

    assert clash.status_code == 409
    detail = clash.json()["detail"]
    assert detail["code"] == "hostname_taken" and [d["id"] for d in detail["devices"]] == [first["id"]]
    assert detail["suggested_names"] == ["karasu-fedora", "karasu-2"]

    adopted = _register(api, UID_B, adopt_device_id=first["id"])
    assert adopted.status_code == 200 and adopted.json()["id"] == first["id"]
    assert _register(api, UID_B, "karasu", name="karasu-2").json()["id"] == first["id"]  # known uid wins


def test_devices_can_be_listed_and_renamed(api):
    a = _register(api).json()
    b = _register(api, UID_B, name="karasu-2").json()
    assert [d["name"] for d in api.get("/api/devices").json()] == ["karasu", "karasu-2"]

    assert api.patch(f"/api/devices/{a['id']}", json={"name": "salotto"}).json()["name"] == "salotto"
    assert api.patch(f"/api/devices/{a['id']}", json={"name": "karasu-2"}).status_code == 409
    assert api.patch(f"/api/devices/{b['id'] + 99}", json={"name": "x"}).status_code == 404


def test_an_uploaded_save_is_listed_downloadable_and_deletable(api):
    device = _register(api).json()

    uploaded = _upload(api, device["id"])
    assert uploaded.status_code == 200 and uploaded.json()["created"] is True
    version = uploaded.json()["version"]
    assert (version["trigger"], version["file_count"], version["device_id"]) == ("quit", 1, device["id"])
    assert version["created_at"].endswith(("Z", "+00:00")) and device["created_at"].endswith(("Z", "+00:00"))

    listing = api.get("/api/games/5/saves").json()
    assert listing["keep_versions"] == 3
    assert [(d["device"]["name"], [v["id"] for v in d["versions"]]) for d in listing["devices"]] == [
        ("karasu", [version["id"]])
    ]

    detail = api.get(f"/api/saves/{version['id']}").json()
    assert detail["manifest"] == [{"path": "users/USER/Saved Games/a.sav", "size": 3}]

    download = api.get(f"/api/saves/{version['id']}/download")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert "Some Game - karasu" in unquote(download.headers["content-disposition"])
    assert zipfile.ZipFile(io.BytesIO(download.content)).read("users/USER/Saved Games/a.sav") == b"one"

    assert api.delete(f"/api/saves/{version['id']}").status_code == 200
    assert api.get(f"/api/saves/{version['id']}").status_code == 404
    assert api.get("/api/games/5/saves").json()["devices"] == []


def test_the_same_content_twice_reports_nothing_new(api):
    device = _register(api).json()
    first = _upload(api, device["id"]).json()
    again = _upload(api, device["id"]).json()
    assert again["created"] is False and again["version"]["id"] == first["version"]["id"]


def test_each_machine_keeps_its_own_newest_versions(api):
    a = _register(api).json()
    b = _register(api, UID_B, name="karasu-2").json()
    for n in range(5):
        _upload(api, a["id"], {"s.sav": str(n).encode()})
    _upload(api, b["id"], {"s.sav": b"b"})

    by_device = {
        d["device"]["name"]: len(d["versions"]) for d in api.get("/api/games/5/saves").json()["devices"]
    }
    assert by_device == {"karasu": 3, "karasu-2": 1}


def test_bad_uploads_are_refused(api, monkeypatch):
    device = _register(api).json()

    junk = api.post(
        "/api/games/5/saves", params={"device_id": device["id"]}, files={"file": ("a.zip", b"nope")}
    )
    assert junk.status_code == 422 and "not a zip" in junk.json()["detail"]

    evil = _upload(api, device["id"], {"../evil.sav": b"x"})
    assert evil.status_code == 422 and "unsafe path" in evil.json()["detail"]

    assert _upload(api, device["id"], trigger="whenever").status_code == 422
    assert _upload(api, device["id"] + 99).status_code == 404
    assert _upload(api, device["id"], game_id=404).status_code == 404

    monkeypatch.setattr(saves, "MAX_SAVE_UPLOAD_BYTES", 10)
    assert _upload(api, device["id"]).status_code == 413
    assert api.get("/api/games/5/saves").json()["devices"] == []


def test_saves_belong_to_their_user_and_respect_hidden_libraries(api):
    device = _register(api).json()
    version = _upload(api, device["id"]).json()["version"]

    api.app_.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=2, is_admin=False, hidden_library_ids=[]
    )
    assert api.get(f"/api/saves/{version['id']}").status_code == 404
    assert api.get(f"/api/saves/{version['id']}/download").status_code == 404
    assert api.delete(f"/api/saves/{version['id']}").status_code == 404
    assert api.get("/api/games/5/saves").json()["devices"] == []
    assert _upload(api, device["id"]).status_code == 404  # not their device

    api.app_.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, is_admin=False, hidden_library_ids=[GAME.library_id]
    )
    assert api.get("/api/games/5/saves").status_code == 404


def test_a_game_was_last_played_when_its_newest_version_was_made(api):
    from handler.database import db_saves_handler

    device = _register(api).json()
    assert db_saves_handler.last_saved_by_game(1) == {}

    version = _upload(api, device["id"]).json()["version"]
    _upload(api, device["id"], {"s.sav": b"later"})

    played = db_saves_handler.last_saved_by_game(1)
    assert list(played) == [5] and played[5] is not None and version["id"]
    assert db_saves_handler.last_saved_by_game(2) == {}  # another user's games are not this one's


def test_a_game_was_last_played_on_the_machine_that_made_the_newest_version(api):
    from handler.database import db_saves_handler

    a = _register(api).json()
    b = _register(api, UID_B, name="deck").json()
    assert db_saves_handler.last_saved_on_by_game(1) == {}

    _upload(api, a["id"])
    assert db_saves_handler.last_saved_on_by_game(1) == {5: "karasu"}
    _upload(api, b["id"], {"s.sav": b"later, on the other machine"})
    assert db_saves_handler.last_saved_on_by_game(1) == {5: "deck"}
    assert db_saves_handler.last_saved_on_by_game(2) == {}


def test_a_new_version_notifies_but_the_same_content_again_does_not(api, monkeypatch):
    sent = []
    monkeypatch.setattr(saves_endpoints, "notify_save_synced", lambda *args: sent.append(args))
    device = _register(api).json()

    _upload(api, device["id"], trigger="quit")
    _upload(api, device["id"], trigger="quit")  # nothing new: no second notification

    assert sent == [(1, 5, "karasu", "quit", 1)]
