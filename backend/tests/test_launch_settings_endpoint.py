from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import devices as devices_endpoints
from endpoints import launch_settings as launch_endpoints
from endpoints import saves as saves_endpoints
from handler.auth import get_current_user
from models.game_launch_settings import PROTON_MAX_LENGTH, WINECFG_REG_MAX_LENGTH

GAME = SimpleNamespace(id=5, name="Some Game", library_id=1)
REG = 'Windows Registry Editor Version 5.00\n\n[HKEY_CURRENT_USER\\Software\\Wine\\DllOverrides]\n"d3d9"="native"\n'


@pytest.fixture
def api(db, monkeypatch):
    monkeypatch.setattr(
        saves_endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME if i == 5 else None)
    )
    app = FastAPI()
    app.include_router(devices_endpoints.router, prefix="/api")
    app.include_router(launch_endpoints.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _device(api, uid="uid-aaaaaaaa", hostname="karasu"):
    return api.post("/api/devices/register", json={"client_uid": uid, "hostname": hostname}).json()["id"]


def test_nothing_is_set_until_a_client_saves_something(api):
    device = _device(api)

    body = api.get("/api/games/5/launch-settings", params={"device_id": device}).json()

    assert body == {"shared": None, "device": None, "devices": []}


def test_a_device_setting_is_kept_apart_from_the_one_shared_by_all(api):
    karasu = _device(api)
    deck = _device(api, "uid-bbbbbbbb", "deck")

    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "GE-Proton9-1"})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": deck, "proton": "UMU-Proton-10.0-4"})

    mine = api.get("/api/games/5/launch-settings", params={"device_id": karasu}).json()
    theirs = api.get("/api/games/5/launch-settings", params={"device_id": deck}).json()
    assert mine["shared"]["proton"] == theirs["shared"]["proton"] == "GE-Proton9-1"
    assert mine["device"] is None and theirs["device"]["proton"] == "UMU-Proton-10.0-4"


def test_a_put_changes_only_the_fields_it_sends_and_null_clears_one(api):
    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "GE-Proton9-1", "winecfg_reg": REG})

    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "GE-Proton9-2"})
    kept = api.get("/api/games/5/launch-settings").json()["shared"]
    assert (kept["proton"], kept["winecfg_reg"]) == ("GE-Proton9-2", REG)

    api.put("/api/games/5/launch-settings", json={"scope": "all", "winecfg_reg": None})
    cleared = api.get("/api/games/5/launch-settings").json()["shared"]
    assert (cleared["proton"], cleared["winecfg_reg"]) == ("GE-Proton9-2", None)


def test_saving_twice_keeps_one_shared_row(api, db):
    from sqlalchemy import text

    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "a"})
    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "b"})

    with db.connect() as connection:
        assert connection.execute(text("select count(*) from game_launch_settings")).scalar() == 1


def test_a_device_that_is_not_the_users_is_refused(api):
    assert api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": 99}).status_code == 404
    assert api.get("/api/games/5/launch-settings", params={"device_id": 99}).status_code == 404


def test_the_device_scope_needs_a_device(api):
    assert api.put("/api/games/5/launch-settings", json={"scope": "device"}).status_code == 422
    assert api.delete("/api/games/5/launch-settings", params={"scope": "device"}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "all", "proton": "x" * (PROTON_MAX_LENGTH + 1)},
        {"scope": "all", "winecfg_reg": "x" * (WINECFG_REG_MAX_LENGTH + 1)},
        {"scope": "all", "executable": "/bin/sh"},
        {"scope": "all", "winetricks": ["vcrun2019; rm -rf ~"]},
        {"scope": "all", "winetricks": ["-q"]},
        {"scope": "all", "winetricks": ["Vcrun2019"]},
        {"scope": "all", "winetricks": [f"v{n}" for n in range(101)]},
    ],
)
def test_oversized_or_unknown_fields_are_refused(api, body):
    assert api.put("/api/games/5/launch-settings", json=body).status_code == 422


def test_an_unknown_game_is_a_404(api):
    assert api.get("/api/games/6/launch-settings").status_code == 404


def test_deleting_removes_only_the_chosen_scope(api):
    device = _device(api)
    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "a"})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": device, "proton": "b"})

    api.delete("/api/games/5/launch-settings", params={"scope": "device", "device_id": device})

    body = api.get("/api/games/5/launch-settings", params={"device_id": device}).json()
    assert body["device"] is None and body["shared"]["proton"] == "a"


def test_the_answer_lists_each_of_the_users_devices_that_has_settings_for_the_web_ui(api):
    karasu = _device(api)
    deck = _device(api, "uid-bbbbbbbb", "deck")
    _device(api, "uid-cccccccc", "silent")  # nothing set: not listed
    api.put("/api/games/5/launch-settings", json={"scope": "all", "proton": "GE-Proton9-1"})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": deck, "winecfg_reg": REG})

    body = api.get("/api/games/5/launch-settings").json()

    assert body["shared"]["proton"] == "GE-Proton9-1"
    assert [(d["device"]["name"], d["proton"], d["winecfg_reg"]) for d in body["devices"]] == [("deck", None, REG)]
    assert karasu != deck


def test_winetricks_verbs_are_kept_once_each_in_order_for_a_scope_and_listed_for_the_web_ui(api):
    deck = _device(api, "uid-bbbbbbbb", "deck")
    api.put("/api/games/5/launch-settings", json={"scope": "all", "winetricks": ["vcrun2019", "d3dx9", "vcrun2019"]})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": deck, "winetricks": ["dotnet48"]})

    body = api.get("/api/games/5/launch-settings", params={"device_id": deck}).json()

    assert body["shared"]["winetricks"] == ["vcrun2019", "d3dx9"] and body["device"]["winetricks"] == ["dotnet48"]
    assert [(d["device"]["name"], d["winetricks"]) for d in body["devices"]] == [("deck", ["dotnet48"])]

    api.put("/api/games/5/launch-settings", json={"scope": "all", "winetricks": None})
    assert api.get("/api/games/5/launch-settings").json()["shared"]["winetricks"] is None
