import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import backups as backups_endpoints
from handler import backups
from handler.auth import get_current_user
from handler.database import db_settings_handler
from models.settings import Settings


@pytest.fixture
def api(monkeypatch, tmp_path):
    data, volume = tmp_path / "mog", tmp_path / "backups"
    data.mkdir()
    volume.mkdir()
    db = data / "mog.db"
    with sqlite3.connect(db) as connection:
        connection.execute("create table alembic_version (version_num text)")
    for name, value in (("SQLITE_PATH", str(db)), ("RESOURCES_BASE_PATH", str(data / "resources")), ("SAVES_BASE_PATH", str(data / "saves")), ("BACKUP_PATH", str(volume))):
        monkeypatch.setattr(backups, name, value)
    monkeypatch.setattr(backups_endpoints, "BACKUP_PATH", str(volume))
    monkeypatch.setattr(db_settings_handler, "get_settings", lambda: Settings())
    app = FastAPI()
    app.include_router(backups_endpoints.router, prefix="/api")
    who = {"user": SimpleNamespace(id=1, is_admin=True)}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    client = TestClient(app)
    client.who, client.volume = who, volume
    return client


def test_the_overview_says_what_the_volume_and_the_switches_are_and_lists_the_backups(api):
    api.post("/api/backups")

    body = api.get("/api/backups").json()

    assert (body["available"], body["reason"], body["daily"], body["daily_saves"], body["hour"]) == (True, None, True, False, 3)
    assert body["keep"] == {"daily": 7, "pre-update": 5, "manual": 10} and body["last_error"] is None
    assert [(b["kind"], b["includes_saves"]) for b in body["backups"]] == [("manual", False)]


def test_a_manual_backup_can_hold_the_saves_and_comes_back_in_the_answer(api):
    made = api.post("/api/backups", params={"saves": "true"})

    assert made.status_code == 200 and made.json()["includes_saves"] is True and made.json()["name"].endswith("-saves.tar.gz")


def test_without_a_volume_the_overview_says_why_and_a_manual_backup_is_a_503(api, monkeypatch, tmp_path):
    monkeypatch.setattr(backups, "BACKUP_PATH", str(tmp_path / "missing"))

    body = api.get("/api/backups").json()
    assert body["available"] is False and "No backup volume" in body["reason"] and body["backups"] == []
    assert api.post("/api/backups").status_code == 503


def test_a_backup_made_while_another_runs_is_a_409(api):
    assert backups._lock.acquire(blocking=False)
    try:
        assert api.post("/api/backups").status_code == 409
    finally:
        backups._lock.release()


def test_a_backup_can_be_downloaded_and_deleted_and_nothing_else_in_the_folder_can(api):
    name = api.post("/api/backups").json()["name"]
    (api.volume / "secret.txt").write_text("not a backup")

    download = api.get(f"/api/backups/{name}/download")
    assert download.status_code == 200 and download.content[:2] == b"\x1f\x8b"  # a gzip
    assert api.get("/api/backups/secret.txt/download").status_code == 404
    assert api.delete("/api/backups/secret.txt").status_code == 404
    assert (api.volume / "secret.txt").exists()

    assert api.delete(f"/api/backups/{name}").status_code == 200
    assert api.get(f"/api/backups/{name}/download").status_code == 404


def test_only_an_administrator_can_use_any_of_it(api):
    api.who["user"] = SimpleNamespace(id=2, is_admin=False)

    assert api.get("/api/backups").status_code == 403
    assert api.post("/api/backups").status_code == 403
    assert api.get("/api/backups/mog-manual-20261012-120000.tar.gz/download").status_code == 403
    assert api.delete("/api/backups/mog-manual-20261012-120000.tar.gz").status_code == 403
    assert list(api.volume.iterdir()) == []


def test_the_uploaded_artwork_is_in_a_backup_but_not_the_icons_read_from_the_games_files(api, tmp_path):
    import tarfile

    media = tmp_path / "mog" / "resources" / "media" / "5"
    (media / "found").mkdir(parents=True)
    (media / "icon-0123456789ab.png").write_bytes(b"mine")
    (media / "found" / "0123456789ab.png").write_bytes(b"found")

    made = api.post("/api/backups").json()

    with tarfile.open(api.volume / made["name"]) as tar:
        names = tar.getnames()
    assert "resources/media/5/icon-0123456789ab.png" in names
    assert not [n for n in names if "/found" in n]
