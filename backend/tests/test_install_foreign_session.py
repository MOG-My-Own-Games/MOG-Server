"""Starting an install while another user's runs must not hand that user's installer display (address and keys) over."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import install
from handler.auth import get_current_user
from models.install_session import InstallSessionState

GAME = SimpleNamespace(id=5, name="Some Game", fs_name="Some Game", library_id=1)


def _client(monkeypatch, running, user_id):
    monkeypatch.setattr(install, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME))
    monkeypatch.setattr(
        install, "db_install_session_handler", SimpleNamespace(get_sessions_for_game=lambda game_id: [running])
    )
    monkeypatch.setattr(install, "_session_schema", lambda session: {"id": session.id, "vnc_url": session.vnc_url})
    app = FastAPI()
    app.include_router(install.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, is_admin=False, hidden_library_ids=[])
    return TestClient(app)


@pytest.fixture
def running():
    return SimpleNamespace(id=9, user_id=1, state=InstallSessionState.INSTALLING, vnc_url="/vnc?token=secret")


def test_another_user_is_refused_and_sees_no_vnc_address(monkeypatch, running):
    response = _client(monkeypatch, running, user_id=2).post("/api/games/5/install", json={})
    assert response.status_code == 409
    assert "secret" not in response.text


def test_the_owner_still_gets_their_own_session_back(monkeypatch, running):
    import asyncio

    _client(monkeypatch, running, user_id=1)
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    result = asyncio.run(install.start_install_session(user, 5, install.InstallStartForm()))
    assert result["id"] == 9
