import io
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import games as games_endpoints
from handler import mods
from handler.auth import get_current_user


@pytest.fixture
def game_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "MODS_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(mods, "_jobs", {})
    root = tmp_path / "Game"
    (root / "Mods" / "mod1").mkdir(parents=True)
    (root / "Mods" / "mod1" / "mod1file.zip").write_bytes(b"z" * 300)
    (root / "Mods" / "mod1" / "readme.txt").write_text("hello " * 50)
    (root / "Mods" / "mod2.zip").write_bytes(b"y" * 120)
    (root / "Mods" / "empty").mkdir()
    (root / "Mods" / ".hidden").write_text("x")
    (root / "Mods" / "notes.txt").write_text("a loose file")
    (root / "Saves").mkdir()
    (root / "Saves" / "not-a-mod.bin").write_bytes(b"s")
    (root / "game.exe").write_bytes(b"exe")
    return root


def _wait(job, timeout=10):
    done = threading.Event()
    for _ in range(int(timeout * 100)):
        if job.state != "zipping":
            return
        done.wait(0.01)
    raise AssertionError("the zip did not finish")


def test_every_top_level_entry_of_the_mods_folder_is_one_mod_not_what_is_inside(game_root):
    found = {m.name: m for m in mods.list_mods(game_root)}

    assert sorted(found) == ["mod1", "mod2.zip", "notes.txt"]  # not mod1file.zip, readme.txt, empty or .hidden
    assert (found["mod1"].kind, found["mod1"].file_count) == (mods.KIND_FOLDER, 2)
    assert found["mod1"].size_bytes == 300 + len("hello " * 50)
    assert found["mod2.zip"].kind == mods.KIND_ARCHIVE and found["notes.txt"].kind == mods.KIND_FILE


def test_a_game_without_a_mods_folder_has_no_mods(tmp_path):
    (tmp_path / "G").mkdir()
    (tmp_path / "G" / "game.exe").write_bytes(b"x")
    assert mods.list_mods(tmp_path / "G") == [] and mods.list_mods(tmp_path / "nope") == []


def test_a_mod_is_found_only_by_the_name_of_a_real_entry(game_root):
    assert mods.find_mod(game_root, "mod1").kind == mods.KIND_FOLDER
    for bad in ("../game.exe", "mod1/mod1file.zip", "..", "Mods", "", "mod3"):
        assert mods.find_mod(game_root, bad) is None


def test_a_folder_is_zipped_under_its_own_name_with_progress_and_a_callback(game_root):
    mod = mods.find_mod(game_root, "mod1")
    finished = []

    job = mods.start_zip(5, mod, user_id=3, on_done=finished.append)
    _wait(job)

    assert job.state == "ready" and job.bytes_done == mod.size_bytes == job.bytes_total
    with zipfile.ZipFile(job.path) as z:
        assert sorted(z.namelist()) == ["mod1/mod1file.zip", "mod1/readme.txt"]
        assert z.read("mod1/mod1file.zip") == b"z" * 300
    assert finished == [None]
    assert not list(job.path.parent.glob("*.part"))


def test_the_same_content_is_not_zipped_again_but_a_changed_folder_is(game_root):
    mod = mods.find_mod(game_root, "mod1")
    first = mods.start_zip(5, mod, 3)
    _wait(first)

    assert mods.start_zip(5, mod, 3) is first  # nothing changed: the zip made is reused

    (game_root / "Mods" / "mod1" / "new.txt").write_text("one more")
    again = mods.start_zip(5, mods.find_mod(game_root, "mod1"), 3)
    assert again is not first
    _wait(again)
    with zipfile.ZipFile(again.path) as z:
        assert "mod1/new.txt" in z.namelist()


def test_a_zip_that_fails_is_reported(game_root, monkeypatch):
    mod = mods.find_mod(game_root, "mod1")
    monkeypatch.setattr(mods.zipfile, "ZipFile", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    finished = []

    job = mods.start_zip(5, mod, 3, on_done=finished.append)
    _wait(job)

    assert job.state == "failed" and "disk full" in job.error and finished == ["disk full"]


@pytest.fixture
def api(game_root, monkeypatch):
    game = SimpleNamespace(id=5, name="Some Game", library_id=1, fs_name="Game", library=SimpleNamespace(root_path=str(game_root.parent)))
    monkeypatch.setattr(games_endpoints.db_game_handler, "get_game", lambda i: game if i == 5 else None)
    notices = []
    monkeypatch.setattr(games_endpoints, "notify_mod_zipped", lambda *args: notices.append(args))
    monkeypatch.setattr(games_endpoints, "notify_mod_downloaded", lambda *args: notices.append(("downloaded", *args)))
    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    client.notices = notices
    return client


def test_the_mods_are_listed_and_an_archive_is_ready_and_downloadable_as_it_is(api):
    listing = api.get("/api/games/5/mods").json()["mods"]
    assert [m["name"] for m in listing] == ["mod1", "mod2.zip", "notes.txt"]

    prepared = api.post("/api/games/5/mods/mod2.zip/prepare").json()
    assert prepared["state"] == "ready" and prepared["bytes_total"] == 120
    download = api.get("/api/games/5/mods/mod2.zip/download")
    assert download.status_code == 200 and download.content == b"y" * 120


def test_a_folder_is_zipped_on_request_then_downloaded_and_the_user_is_told(api):
    assert api.get("/api/games/5/mods/mod1/status").json()["state"] == "idle"
    assert api.get("/api/games/5/mods/mod1/download").status_code == 409  # not asked for yet

    first = api.post("/api/games/5/mods/mod1/prepare").json()
    assert first["state"] in ("zipping", "ready")
    job = mods.job_for(5, "mod1")
    _wait(job)

    assert api.get("/api/games/5/mods/mod1/status").json()["state"] == "ready"
    download = api.get("/api/games/5/mods/mod1/download")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(download.content)).namelist() == ["mod1/mod1file.zip", "mod1/readme.txt"]
    assert api.notices == [(1, 5, "mod1", None)]


def test_a_finished_download_is_told_in_the_notifications_with_the_machine(api):
    assert api.post("/api/games/5/mods/mod2.zip/downloaded", params={"machine": "karasu"}).status_code == 200
    assert api.post("/api/games/5/mods/mod2.zip/downloaded").status_code == 200
    assert api.post("/api/games/5/mods/nope/downloaded").status_code == 404
    assert api.notices == [("downloaded", 1, 5, "mod2.zip", "karasu"), ("downloaded", 1, 5, "mod2.zip", None)]


def test_an_unknown_mod_or_game_is_a_404(api):
    assert api.get("/api/games/5/mods/nope/status").status_code == 404
    assert api.post("/api/games/5/mods/..%2Fgame.exe/prepare").status_code == 404
    assert api.get("/api/games/9/mods").status_code == 404


def test_a_zip_can_be_cancelled_and_leaves_nothing_behind(game_root, monkeypatch):
    mod = mods.find_mod(game_root, "mod1")
    gate = threading.Event()
    monkeypatch.setattr(mods, "_forget_old_zips", lambda: gate.wait(5))  # holds the worker until the test has cancelled
    finished = []
    job = mods.start_zip(5, mod, 3, on_done=finished.append)
    assert mods.cancel_zip(5, "mod1") is True
    gate.set()
    for _ in range(500):
        if mods.job_for(5, "mod1") is None:
            break
        threading.Event().wait(0.01)

    assert mods.job_for(5, "mod1") is None and job.cancel.is_set()  # "idle" again
    assert finished == []  # a cancelled zip is not a failure, nobody is told
    assert not list(Path(mods.MODS_CACHE_PATH).rglob("*.zip*"))
    assert mods.cancel_zip(5, "mod1") is False  # nothing is running now


def test_the_cancel_endpoint_stops_a_running_zip_and_does_nothing_for_an_archive(api, game_root, monkeypatch):
    assert api.post("/api/games/5/mods/mod2.zip/cancel").json()["state"] == "ready"  # an archive is never "zipping"
    called = []
    monkeypatch.setattr(mods, "cancel_zip", lambda gid, name: called.append((gid, name)))
    assert api.post("/api/games/5/mods/mod1/cancel").json()["state"] == "idle"
    assert called == [(5, "mod1")]
