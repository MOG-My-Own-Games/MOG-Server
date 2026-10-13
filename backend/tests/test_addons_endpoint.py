import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import addons as endpoints
from handler import addons as addons_handler
from handler.auth import get_current_user

GAME = SimpleNamespace(id=5, name="Some Game")


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def api(db, tmp_path, monkeypatch):
    monkeypatch.setattr(addons_handler, "ADDONS_PATH", str(tmp_path / "addons"))
    monkeypatch.setattr(endpoints, "ADDONS_PATH", str(tmp_path / "addons"))
    monkeypatch.setattr(endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME if i == 5 else None))
    app = FastAPI()
    app.include_router(endpoints.router, prefix="/api")
    state = SimpleNamespace(user=SimpleNamespace(id=1, is_admin=False))
    app.dependency_overrides[get_current_user] = lambda: state.user
    client = TestClient(app)
    client.state = state
    return client


def _upload(api, data: bytes, category="patch", name="Patch 2.0"):
    return api.post(
        "/api/games/5/addons",
        data={"category": category, "name": name},
        files={"file": ("p.zip", data, "application/zip")},
    )


def test_an_uploaded_patch_is_listed_and_can_be_downloaded(api):
    created = _upload(api, _zip_bytes({"Game/a.pak": b"x"}))

    assert created.status_code == 201
    listed = api.get("/api/games/5/addons").json()
    assert [(a["category"], a["name"], a["file_count"]) for a in listed] == [("patch", "Patch 2.0", 1)]

    got = api.get(f"/api/games/5/addons/{created.json()['id']}/download")
    assert got.status_code == 200 and zipfile.ZipFile(io.BytesIO(got.content)).namelist() == ["Game/a.pak"]


def test_an_upload_that_is_not_a_zip_is_refused(api):
    assert _upload(api, b"not a zip").status_code == 400


def test_an_unknown_category_is_refused(api):
    assert _upload(api, _zip_bytes({"a": b"x"}), category="save").status_code == 400


def test_an_upload_over_the_limit_is_refused(api, monkeypatch):
    monkeypatch.setattr(endpoints, "ADDONS_MAX_BYTES", 10)

    assert _upload(api, _zip_bytes({"a": b"x" * 100})).status_code == 413


def test_the_list_of_all_addons_spans_games(api):
    _upload(api, _zip_bytes({"a": b"x"}))

    assert len(api.get("/api/games/addons").json()) == 1


def test_only_an_admin_or_the_uploader_can_delete(api):
    created = _upload(api, _zip_bytes({"a": b"x"})).json()
    url = f"/api/games/5/addons/{created['id']}"

    api.state.user = SimpleNamespace(id=2, is_admin=False)
    assert api.delete(url).status_code == 403

    api.state.user = SimpleNamespace(id=2, is_admin=True)
    assert api.delete(url).status_code == 200
    assert api.get("/api/games/5/addons").json() == []


def test_an_addon_is_not_found_under_another_game(api):
    created = _upload(api, _zip_bytes({"a": b"x"})).json()

    assert api.get(f"/api/games/9/addons/{created['id']}/download").status_code == 404
