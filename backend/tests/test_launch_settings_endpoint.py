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
    user = SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    client.app_user = user
    return client


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
        # a setting's value goes on a command line too
        {"scope": "all", "winetricks": ["vd=1280 x 1024"]},
        {"scope": "all", "winetricks": ["vd=1280x1024; rm -rf ~"]},
        {"scope": "all", "winetricks": ["vd=$(id)"]},
        {"scope": "all", "winetricks": ["renderer=gdi|sh"]},
        {"scope": "all", "winetricks": ["renderer=gdi&sh"]},
        {"scope": "all", "winetricks": ["renderer=`id`"]},
        {"scope": "all", "winetricks": ["renderer='gdi'"]},
        {"scope": "all", "winetricks": ["vd=../../etc"]},
        {"scope": "all", "winetricks": ["vd=a\nb"]},
        {"scope": "all", "winetricks": ["vd="]},
        {"scope": "all", "winetricks": ["=gdi"]},
        {"scope": "all", "winetricks": ["vd=a=b"]},
        {"scope": "all", "winetricks": ["VD=gdi"]},
        {"scope": "all", "winetricks": ["-vd=gdi"]},
        {"scope": "all", "winetricks": ["vd=" + "x" * 41]},
        {"scope": "all", "winetricks": ["v" * 41 + "=gdi"]},
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


def test_winetricks_settings_with_a_value_sit_in_the_list_beside_the_flags(api):
    api.put("/api/games/5/launch-settings", json={"scope": "all", "winetricks": ["vcrun2019", "renderer=gdi", "vd=1280x1024", "mwo=force"]})

    body = api.get("/api/games/5/launch-settings").json()

    assert body["shared"]["winetricks"] == ["vcrun2019", "renderer=gdi", "vd=1280x1024", "mwo=force"]


def test_a_setting_has_one_value_a_later_one_replaces_an_earlier_in_the_place_it_had(api):
    sent = ["vd=1280x1024", "vcrun2019", "renderer=gdi", "vd=1920x1080", "vcrun2019"]
    api.put("/api/games/5/launch-settings", json={"scope": "all", "winetricks": sent})

    assert api.get("/api/games/5/launch-settings").json()["shared"]["winetricks"] == ["vd=1920x1080", "vcrun2019", "renderer=gdi"]


def test_flags_still_work_as_before_and_the_values_a_device_sets_are_its_own_row(api):
    deck = _device(api, "uid-bbbbbbbb", "deck")
    api.put("/api/games/5/launch-settings", json={"scope": "all", "winetricks": ["vcrun2019", "vd=1280x1024"]})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": deck, "winetricks": ["vd=1920x1080"]})

    body = api.get("/api/games/5/launch-settings", params={"device_id": deck}).json()

    assert body["shared"]["winetricks"] == ["vcrun2019", "vd=1280x1024"]  # the rows are kept apart; a client lets the device's value win
    assert body["device"]["winetricks"] == ["vd=1920x1080"]


def test_launch_arguments_are_tags_kept_per_scope_and_listed_for_the_web_ui(api):
    device = _device(api)

    api.put("/api/games/5/launch-settings", json={"scope": "all", "launch_args": ["-windowed", "-res 1280 720", "-windowed"]})
    api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": device, "launch_args": ["-savetouserdir"]})
    body = api.get("/api/games/5/launch-settings", params={"device_id": device}).json()

    assert body["shared"]["launch_args"] == ["-windowed", "-res 1280 720"]  # once each, in order
    assert body["device"]["launch_args"] == ["-savetouserdir"]
    assert body["devices"][0]["launch_args"] == ["-savetouserdir"]


def test_a_null_clears_the_launch_arguments_and_a_tag_is_one_short_line(api):
    api.put("/api/games/5/launch-settings", json={"scope": "all", "launch_args": ["-windowed"]})

    assert api.put("/api/games/5/launch-settings", json={"scope": "all", "launch_args": None}).json()["launch_args"] is None
    for bad in (["-a\n-b"], ["x" * 201], [" -a"], ["-a "], [""], ["-a\x00"], "-windowed", [f"-t{n}" for n in range(51)]):
        assert api.put("/api/games/5/launch-settings", json={"scope": "all", "launch_args": bad}).status_code == 422, bad


def test_adding_and_removing_tags_changes_the_list_the_server_holds_so_two_editors_do_not_undo_each_other(api):
    url = "/api/games/5/launch-settings"
    api.put(url, json={"scope": "all", "launch_args": ["-a", "-b", "-c"]})

    # the web page took "-b" out, and a client, which never saw that, put "-d" in: both stay
    api.put(url, json={"scope": "all", "launch_args_remove": ["-b"]})
    after = api.put(url, json={"scope": "all", "launch_args_add": ["-d", "-a"]}).json()

    assert after["launch_args"] == ["-a", "-c", "-d"]  # "-a" was there: not twice; "-d" goes at the end
    assert api.put(url, json={"scope": "all", "launch_args_add": ["-b"], "launch_args_remove": ["-b", "-c"]}).json()["launch_args"] == ["-a", "-d", "-b"]


def test_a_change_to_tags_makes_the_list_when_there_is_none_and_an_empty_one_makes_nothing(api):
    url = "/api/games/5/launch-settings"
    api.put(url, json={"scope": "all", "proton": "GE-Proton10-1"})

    assert api.put(url, json={"scope": "all", "launch_args_add": [], "launch_args_remove": []}).json()["launch_args"] is None
    assert api.put(url, json={"scope": "all", "launch_args_add": ["-w"]}).json()["launch_args"] == ["-w"]


def test_replacing_the_list_and_changing_it_are_not_sent_together(api):
    body = {"scope": "all", "launch_args": ["-a"], "launch_args_add": ["-b"]}

    assert api.put("/api/games/5/launch-settings", json=body).status_code == 422



def test_only_an_admin_sets_what_all_devices_share(api):
    """What is shared is applied on every user's machines, so a plain user may set only what is for their own devices."""
    api.app_user.is_admin = False
    device = _device(api)

    assert api.put("/api/games/5/launch-settings", json={"scope": "all", "launch_args": ["-x"]}).status_code == 403
    assert api.delete("/api/games/5/launch-settings", params={"scope": "all"}).status_code == 403
    assert api.put("/api/games/5/launch-settings", json={"scope": "device", "device_id": device, "launch_args": ["-x"]}).status_code == 200
