from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import devices as devices_endpoints
from endpoints import playtime as playtime_endpoints
from endpoints import saves as saves_endpoints
from handler.auth import get_current_user
from handler.database import db_playtime_handler

GAME = SimpleNamespace(id=5, name="Some Game", library_id=1)
START = datetime(2026, 10, 20, 18, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr(saves_endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME if i == 5 else None))
    app = FastAPI()
    app.include_router(devices_endpoints.router, prefix="/api")
    app.include_router(playtime_endpoints.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    client.app_user = user
    return client


def _post(api, started=START, seconds=3600, **extra):
    return api.post("/api/games/5/playtime", json={"started_at": started.isoformat(), "seconds": seconds, **extra})


def test_a_game_nobody_played_has_no_time(api):
    assert api.get("/api/games/5/playtime").json() == {"game_id": 5, "playtime_seconds": 0, "sessions": 0, "recorded": True}


def test_sessions_add_up(api):
    _post(api, START, 3600)
    body = _post(api, START + timedelta(days=1), 600).json()

    assert body["playtime_seconds"] == 4200 and body["sessions"] == 2 and body["recorded"] is True
    assert api.get("/api/games/5/playtime").json()["playtime_seconds"] == 4200


def test_the_same_session_sent_again_counts_once(api):
    """A client whose reply was lost sends the session again from its outbox: it is told by when it started."""
    _post(api, START, 3600)
    body = _post(api, START, 3600).json()

    assert body["recorded"] is False and body["playtime_seconds"] == 3600 and body["sessions"] == 1


def test_fractions_of_a_second_in_the_start_do_not_make_it_a_new_session(api):
    _post(api, START, 3600)

    assert _post(api, START.replace(microsecond=400000), 3600).json()["recorded"] is False


def test_a_session_is_never_empty_nor_longer_than_a_day_nor_in_the_future(api):
    assert _post(api, seconds=0).status_code == 422
    assert _post(api, seconds=24 * 3600 + 1).status_code == 422
    assert _post(api, datetime.now(timezone.utc) + timedelta(days=3)).status_code == 422
    assert api.get("/api/games/5/playtime").json()["sessions"] == 0


def test_an_unknown_game_is_a_404(api):
    assert api.post("/api/games/9/playtime", json={"started_at": START.isoformat(), "seconds": 60}).status_code == 404


def test_the_device_has_to_be_the_users_own(api):
    assert _post(api, device_id=999).status_code == 404
    device = api.post("/api/devices/register", json={"client_uid": "uid-aaaaaaaa", "hostname": "karasu"}).json()["id"]

    assert _post(api, device_id=device).status_code == 200


def test_the_time_is_each_users_own(api):
    _post(api, START, 3600)

    assert db_playtime_handler.seconds_by_game(1) == {5: 3600}
    assert db_playtime_handler.seconds_by_game(2) == {}
