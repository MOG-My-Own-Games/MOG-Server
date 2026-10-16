from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import about
from handler import changelog
from handler.auth import get_current_user


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(changelog, "CHANGELOG_PATH", tmp_path / "changelog.md")
    monkeypatch.setattr(about, "MOG_VERSION", "1.2.3")
    app = FastAPI()
    app.include_router(about.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2, is_admin=False)
    return TestClient(app), tmp_path / "changelog.md"


def test_about_gives_the_version_and_the_notes_the_build_carries(api):
    client, notes = api
    notes.write_text("## Fixes\n\n- a: b [[abc1234](https://github.com/o/r/commit/abc1234)]\n")

    body = client.get("/api/about").json()

    assert body["version"] == "1.2.3"
    assert body["changelog"].startswith("## Fixes")


def test_about_tells_a_build_without_notes_from_a_version_with_nothing_to_list(api):
    client, notes = api
    assert client.get("/api/about").json()["changelog"] is None

    notes.write_text("")
    assert client.get("/api/about").json()["changelog"] == ""
