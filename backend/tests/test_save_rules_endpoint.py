import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import devices as devices_endpoints
from endpoints import save_rules as rules_endpoints
from endpoints import saves as saves_endpoints
from handler import saves
from handler.auth import get_current_user
from models.game_save_rule import clean_path, covers

GAME = SimpleNamespace(id=5, name="Some Game", fs_name="Some Game", library_id=1, effective_title_key="mog:5")
SHADERS = "users/USER/AppData/Local/Game/shadercache"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def api(db, monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    monkeypatch.setattr(saves_endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: GAME if i == 5 else None))
    app = FastAPI()
    for module in (devices_endpoints, saves_endpoints, rules_endpoints):
        app.include_router(module.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    device = client.post("/api/devices/register", json={"client_uid": "uid-aaaaaaaa", "hostname": "desktop"}).json()
    client.device_id = device["id"]
    return client


def _upload(api, files):
    return api.post(
        "/api/games/5/saves",
        params={"device_id": api.device_id, "trigger": "quit"},
        files={"file": ("save.zip", _zip(files), "application/zip")},
    )


def _members(api, version_id):
    return [f["path"] for f in api.get(f"/api/saves/{version_id}").json()["manifest"]]


def test_a_path_is_added_to_a_list_and_moves_to_the_other_instead_of_being_on_both(api):
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [SHADERS, "game/cache"]})
    api.post("/api/games/5/save-rules", json={"kind": "whitelist", "paths": ["game/cache"]})

    rules = api.get("/api/games/5/save-rules").json()

    assert rules == {"blacklist": [SHADERS], "whitelist": ["game/cache"]}


def test_removing_a_rule_forgets_it_whichever_list_it_was_on(api):
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [SHADERS]})

    rules = api.post("/api/games/5/save-rules/remove", json={"paths": [SHADERS]}).json()

    assert rules == {"blacklist": [], "whitelist": []}


@pytest.mark.parametrize("bad", ["../etc", "/abs/path", "a\\b", "C:/x", "a//b", ""])
def test_a_path_that_is_not_an_archive_key_is_refused(api, bad):
    answer = api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [bad]})

    assert answer.status_code == 422


def test_a_folder_rule_covers_what_is_inside_it_and_not_a_sibling_with_the_same_prefix():
    assert covers("game/cache", "game/cache/a.bin")
    assert covers("game/cache", "game/cache")
    assert not covers("game/cache", "game/cache2/a.bin")
    assert clean_path(" game/cache/ ") == "game/cache"


def test_files_taken_out_of_a_version_leave_the_rest_and_the_row_tells_it(api):
    version = _upload(
        api, {"game/slot1.sav": b"save", f"{SHADERS}/a.bin": b"x" * 100, f"{SHADERS}/b.bin": b"y" * 100}
    ).json()["version"]

    after = api.post(f"/api/saves/{version['id']}/remove-files", json={"paths": [SHADERS]}).json()

    assert after["file_count"] == 1
    assert [f["path"] for f in after["manifest"]] == ["game/slot1.sav"]
    assert after["content_hash"] != version["content_hash"]
    assert after["size_bytes"] < version["size_bytes"]
    assert api.get(f"/api/saves/{version['id']}/download").status_code == 200


def test_a_version_with_nothing_left_after_removing_its_files_is_deleted(api):
    version = _upload(api, {f"{SHADERS}/a.bin": b"x"}).json()["version"]

    answer = api.post(f"/api/saves/{version['id']}/remove-files", json={"paths": [SHADERS]})

    assert answer.json() is None
    assert api.get(f"/api/saves/{version['id']}").status_code == 404


def test_an_upload_is_stored_without_what_is_blacklisted_even_from_a_client_that_did_not_know(api):
    """An older client sends the shader cache it has always sent: the server keeps the saves and not the cache."""
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [SHADERS]})

    version = _upload(api, {"game/slot1.sav": b"save", f"{SHADERS}/a.bin": b"x" * 50}).json()["version"]

    assert _members(api, version["id"]) == ["game/slot1.sav"]
    assert version["file_count"] == 1


def test_an_upload_of_only_blacklisted_files_is_refused(api):
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [SHADERS]})

    assert _upload(api, {f"{SHADERS}/a.bin": b"x"}).status_code == 422


def test_the_rules_of_a_game_are_not_visible_for_a_game_the_user_may_not_see(api, monkeypatch):
    hidden = SimpleNamespace(id=6, name="H", fs_name="H", library_id=9, effective_title_key="mog:6")
    monkeypatch.setattr(saves_endpoints, "db_game_handler", SimpleNamespace(get_game=lambda i: hidden))
    api.app_ = None
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[9])
    api.app.dependency_overrides[get_current_user] = lambda: user

    assert api.get("/api/games/6/save-rules").status_code == 404


@pytest.mark.parametrize(
    "rule, path, hit",
    [
        ("*.png", "game/art/a.png", True),
        ("*.png", "game/art/A.PNG", True),  # a Wine prefix does not tell the case either
        ("*.png", "game/art/a.pngx", False),
        ("cache*", "game/cache2/a.bin", True),  # a name that fits makes the folder and all that is in it
        ("game/*/shaders", "game/x/shaders/a.bin", True),
        ("game/*/shaders", "game/x/y/shaders/a.bin", False),  # `*` stays inside one folder
        ("game/**.bin", "game/x/y/a.bin", True),
        ("slot[0-9].sav", "game/slot3.sav", True),
        ("slot[!0-9].sav", "game/slot3.sav", False),
        ("re:\\.(png|jpg)$", "game/a.jpg", True),
        ("re:^users/", "game/users/a", False),
    ],
)
def test_a_pattern_rule_names_the_paths_it_fits(rule, path, hit):
    assert covers(rule, path) is hit


@pytest.mark.parametrize("bad", ["re:(", "re:", "re:" + "x" * 201, "../*.png", "/a*", "a/[z-a]*"])
def test_a_pattern_that_cannot_work_is_refused(api, bad):
    answer = api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": [bad]})

    assert answer.status_code == 422


def test_a_wildcard_blacklist_keeps_every_png_out_of_an_upload_and_a_regex_does_too(api):
    """`*.png` was a path that matched nothing: the pictures of a port's launcher went up with every save."""
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["*.png", "re:\\.tmp\\d$"]})

    version = _upload(
        api, {"game/slot1.sav": b"save", "game/a/b.png": b"x", "users/USER/Documents/G/c.PNG": b"y", "game/d.tmp3": b"z"}
    ).json()["version"]

    assert _members(api, version["id"]) == ["game/slot1.sav"]


def test_a_wildcard_takes_every_matching_file_out_of_a_stored_version(api):
    version = _upload(api, {"game/slot1.sav": b"save", "game/a/b.png": b"x", "game/c.png": b"y"}).json()["version"]

    after = api.post(f"/api/saves/{version['id']}/remove-files", json={"paths": ["*.png"]}).json()

    assert [f["path"] for f in after["manifest"]] == ["game/slot1.sav"]


def test_a_wildcard_can_be_taken_off_like_any_rule(api):
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["*.png"]})

    rules = api.post("/api/games/5/save-rules/remove", json={"paths": ["*.png"]}).json()

    assert rules["blacklist"] == []


def test_the_blacklisted_files_still_stored_are_listed_once_with_their_versions_and_size(api):
    _upload(api, {"game/slot1.sav": b"a", "game/art/a.png": b"x" * 10, "game/b.png": b"y" * 5})
    _upload(api, {"game/slot1.sav": b"bb", "game/art/a.png": b"x" * 10})
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["*.png"]})

    listed = api.get("/api/games/5/save-rules/blacklisted").json()

    assert listed["versions"] == 2 and listed["bytes"] == 25 and listed["truncated"] is False
    assert listed["files"] == [
        {"path": "game/art/a.png", "size": 20, "versions": 2},
        {"path": "game/b.png", "size": 5, "versions": 1},
    ]


def test_purging_takes_the_blacklisted_files_out_of_every_stored_version_and_deletes_the_ones_left_empty(api):
    """Blacklisting only stopped new uploads: the pictures already stored kept the server big."""
    first = _upload(api, {"game/slot1.sav": b"a", "game/art/a.png": b"x" * 10}).json()["version"]
    second = _upload(api, {"game/art/only.png": b"y" * 5}).json()["version"]
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["*.png"]})

    done = api.post("/api/games/5/save-rules/purge").json()

    assert done == {"versions_changed": 1, "versions_deleted": 1, "files_removed": 2}
    assert _members(api, first["id"]) == ["game/slot1.sav"]
    assert api.get(f"/api/saves/{second['id']}").status_code == 404
    assert api.get("/api/games/5/save-rules/blacklisted").json()["files"] == []


def test_purging_with_nothing_blacklisted_changes_nothing(api):
    version = _upload(api, {"game/slot1.sav": b"a"}).json()["version"]

    done = api.post("/api/games/5/save-rules/purge").json()

    assert done == {"versions_changed": 0, "versions_deleted": 0, "files_removed": 0}
    assert _members(api, version["id"]) == ["game/slot1.sav"]


def test_the_whitelist_is_stronger_than_the_blacklist_so_a_folder_can_be_left_out_but_its_saves(api):
    """Blacklist `game/thisgame` and add `game/thisgame/saves`: everything in the game is left out except its saves."""
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["game/thisgame"]})
    api.post("/api/games/5/save-rules", json={"kind": "whitelist", "paths": ["game/thisgame/saves"]})

    version = _upload(
        api,
        {
            "game/thisgame/shaders/a.bin": b"x",
            "game/thisgame/saves/slot1.sav": b"s",
            "game/thisgame/saves/deep/slot2.sav": b"t",
            "game/other/b.sav": b"o",
        },
    ).json()["version"]

    assert sorted(_members(api, version["id"])) == [
        "game/other/b.sav",
        "game/thisgame/saves/deep/slot2.sav",
        "game/thisgame/saves/slot1.sav",
    ]


def test_a_wildcard_blacklist_gives_way_to_a_whitelisted_folder_and_the_purge_leaves_it_alone(api):
    version = _upload(api, {"game/art/a.png": b"x" * 3, "game/saves/shot.png": b"y" * 4, "game/saves/slot1.sav": b"s"}).json()["version"]
    api.post("/api/games/5/save-rules", json={"kind": "blacklist", "paths": ["*.png"]})
    api.post("/api/games/5/save-rules", json={"kind": "whitelist", "paths": ["game/saves"]})

    listed = api.get("/api/games/5/save-rules/blacklisted").json()
    done = api.post("/api/games/5/save-rules/purge").json()

    assert [f["path"] for f in listed["files"]] == ["game/art/a.png"]
    assert done["files_removed"] == 1
    assert sorted(_members(api, version["id"])) == ["game/saves/shot.png", "game/saves/slot1.sav"]
