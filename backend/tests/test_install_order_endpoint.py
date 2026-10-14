from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endpoints import install
from handler.auth import get_current_user

GAME = SimpleNamespace(id=5, name="Some Game", fs_name="Some Game", library_id=1)


@pytest.fixture
def api(db, tmp_path, monkeypatch):
    game_root = tmp_path / "Some Game"
    for name in ("setup.exe", "patch1.exe", "patch2.exe", "dlc/extra.exe"):
        (game_root / name).parent.mkdir(parents=True, exist_ok=True)
        (game_root / name).write_bytes(b"MZ")

    def resolve(game, rel_path):
        candidate = (game_root / rel_path).resolve()
        if game_root.resolve() not in candidate.parents:
            raise ValueError("Installer path escapes the game's directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"Installer not found: {rel_path}")
        return str(candidate)

    monkeypatch.setattr(
        install,
        "db_game_handler",
        SimpleNamespace(get_game=lambda i: GAME if i == 5 else None),
    )
    monkeypatch.setattr(install, "fs_game_handler", SimpleNamespace(resolve_installer_abs_path=resolve, get_installer_candidates=lambda game: []))

    app = FastAPI()
    app.include_router(install.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    return TestClient(app)


def test_a_game_with_no_saved_order_has_no_steps(api):
    assert api.get("/api/games/5/install/order").json() == {"game_id": 5, "steps": []}


def test_the_order_is_kept_as_sent_and_comes_back_in_the_same_order(api):
    body = {"steps": [{"path": "setup.exe"}, {"path": "patch2.exe"}, {"path": "patch1.exe"}]}

    put = api.put("/api/games/5/install/order", json=body)
    got = api.get("/api/games/5/install/order").json()

    assert put.status_code == 200
    assert [s["path"] for s in got["steps"]] == ["setup.exe", "patch2.exe", "patch1.exe"]
    assert [s["position"] for s in got["steps"]] == [0, 1, 2]


def test_a_put_replaces_the_whole_order(api):
    api.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}, {"path": "patch1.exe"}]})

    api.put("/api/games/5/install/order", json={"steps": [{"path": "patch2.exe"}]})

    assert [s["path"] for s in api.get("/api/games/5/install/order").json()["steps"]] == ["patch2.exe"]


def test_an_empty_list_clears_the_order(api):
    api.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}]})

    api.put("/api/games/5/install/order", json={"steps": []})

    assert api.get("/api/games/5/install/order").json()["steps"] == []


def test_a_source_archive_is_kept_with_its_installer(api):
    api.put(
        "/api/games/5/install/order",
        json={"steps": [{"path": "dlc/extra.exe", "source_path": "setup.exe"}]},
    )

    step = api.get("/api/games/5/install/order").json()["steps"][0]

    assert (step["path"], step["source_path"]) == ("dlc/extra.exe", "setup.exe")


def test_a_path_outside_the_game_directory_is_refused(api):
    response = api.put("/api/games/5/install/order", json={"steps": [{"path": "../../etc/passwd"}]})

    assert response.status_code == 400
    assert api.get("/api/games/5/install/order").json()["steps"] == []


def test_a_missing_installer_is_refused_and_the_saved_order_is_left_alone(api):
    api.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}]})

    response = api.put("/api/games/5/install/order", json={"steps": [{"path": "gone.exe"}]})

    assert response.status_code == 400
    assert [s["path"] for s in api.get("/api/games/5/install/order").json()["steps"]] == ["setup.exe"]


def test_the_same_installer_twice_is_refused(api):
    response = api.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}, {"path": "setup.exe"}]})

    assert response.status_code == 400


def test_more_steps_than_the_limit_are_refused(api):
    from models.install_step import INSTALL_STEPS_MAX

    steps = [{"path": "setup.exe"} for _ in range(INSTALL_STEPS_MAX + 1)]

    assert api.put("/api/games/5/install/order", json={"steps": steps}).status_code == 422


def test_an_unknown_game_is_not_found(api):
    assert api.get("/api/games/99/install/order").status_code == 404
    assert api.put("/api/games/99/install/order", json={"steps": []}).status_code == 404


def test_the_candidates_answer_carries_the_saved_order(api):
    api.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}, {"path": "patch1.exe"}]})

    body = api.get("/api/games/5/install/candidates").json()

    assert [s["path"] for s in body["order"]] == ["setup.exe", "patch1.exe"]



@pytest.fixture
def started(api, monkeypatch):
    """The start endpoint with the runner's queue and the cache clean-up stubbed out; the sessions are real rows."""
    monkeypatch.setattr(install, "start_or_queue", lambda sid: True)
    monkeypatch.setattr(install, "purge_superseded_sessions", lambda *a: None)
    monkeypatch.setattr(install, "clear_session_cache", lambda sid: None)
    return api


def test_a_start_with_steps_keeps_the_chain_on_the_session(started):
    body = {"steps": [{"path": "setup.exe"}, {"path": "patch1.exe"}]}

    session = started.post("/api/games/5/install", json=body).json()

    assert session["step_index"] == 0 and session["step_total"] == 2
    assert session["installer_path"] == "setup.exe"


def test_a_start_without_a_choice_uses_the_saved_order(started):
    started.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}, {"path": "patch2.exe"}]})

    session = started.post("/api/games/5/install", json={}).json()

    assert session["step_total"] == 2 and session["installer_path"] == "setup.exe"


def test_a_start_naming_one_installer_ignores_the_saved_order(started):
    started.put("/api/games/5/install/order", json={"steps": [{"path": "setup.exe"}, {"path": "patch2.exe"}]})

    session = started.post("/api/games/5/install", json={"installer_path": "patch1.exe"}).json()

    assert session["step_total"] == 1 and session["installer_path"] == "patch1.exe"


def test_a_start_with_a_bad_step_is_refused(started):
    response = started.post("/api/games/5/install", json={"steps": [{"path": "../outside.exe"}]})

    assert response.status_code == 400
