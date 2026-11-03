from types import SimpleNamespace

import pytest
from endpoints import games as games_endpoints
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from handler import scrape_handler
from handler.auth import get_current_user, require_admin
from handler.database import db_game_handler, db_library_handler
from handler.filesystem import fs_game_handler
from handler.scan_handler import scan_library
from handler.sizes import game_sizes
from models.game import Game
from models.library import Library


@pytest.fixture
def api(db, monkeypatch):
    """The games router on a minimal app, signed in as a plain user; the scrape is recorded, not run."""
    scraped = []
    monkeypatch.setattr(games_endpoints, "scrape_imported_in_background", scraped.append)
    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    client.scraped = scraped
    return client


def test_importing_a_game_makes_an_entry_with_no_library_a_second_machine_shares(api):
    first = api.post("/api/games/import", json={"name": "Hollow  Knight"})

    assert first.status_code == 200
    body = first.json()
    assert (body["imported"], body["library_id"], body["missing_from_fs"]) == (True, None, True)
    assert body["name"] == "Hollow Knight"
    assert api.scraped == [body["id"]]

    # the other machine, typed a bit differently
    again = api.post("/api/games/import", json={"name": "hollow knight!"})

    assert again.json()["id"] == body["id"]
    assert len(api.get("/api/games").json()) == 1
    assert api.scraped == [body["id"]]  # not looked up twice


def test_a_name_without_letters_or_digits_is_refused(api):
    assert api.post("/api/games/import", json={"name": " -- "}).status_code == 422


def test_an_imported_game_is_found_again_by_the_name_it_was_given_after_a_rename(api):
    game = api.post("/api/games/import", json={"name": "hk"}).json()
    db_game_handler.update_game(game["id"], {"name": "Hollow Knight"})

    assert api.post("/api/games/import", json={"name": "HK"}).json()["id"] == game["id"]


def test_an_imported_game_has_no_files_and_what_needs_them_says_so(api):
    game = api.post("/api/games/import", json={"name": "Celeste"}).json()

    answer = api.get(f"/api/games/{game['id']}/files")

    assert answer.status_code == 409 and answer.json()["detail"] == {"code": "no_files"}
    with pytest.raises(HTTPException) as refused:
        fs_game_handler.get_game_root_abs_path(db_game_handler.get_game(game["id"]))
    assert refused.value.detail == {"code": "no_files"}


def test_the_sizes_of_an_imported_game_count_only_what_its_saves_take(api):
    game = api.post("/api/games/import", json={"name": "Celeste"}).json()

    sizes = game_sizes(db_game_handler.get_game(game["id"]))

    assert (sizes.installer, sizes.cache, sizes.files, sizes.total) == (0, 0, 0, 0)


def test_an_imported_game_still_wants_its_cover_but_a_missing_one_does_not(monkeypatch):
    monkeypatch.setattr(scrape_handler.igdb_handler, "is_configured", lambda: True)
    monkeypatch.setattr(scrape_handler.sgdb_handler, "is_configured", lambda: True)
    imported = Game(library_id=None, fs_name="A", name="A", imported=True, missing_from_fs=True)
    gone = Game(library_id=1, fs_name="B", name="B", imported=False, missing_from_fs=True)

    assert scrape_handler.needs_scrape(imported) is True
    assert scrape_handler.needs_scrape(gone) is False


def test_an_imported_game_still_moves_the_revision_of_a_user_with_hidden_libraries(db):
    hidden = db_library_handler.add_library(Library(name="restricted", root_path="/library/restricted"))
    before = db_game_handler.revision([hidden.id])

    db_game_handler.add_game(
        Game(library_id=None, fs_name="C", name="C", imported=True, missing_from_fs=True)
    )

    assert db_game_handler.revision([hidden.id]) != before


def test_a_scan_that_finds_a_folder_named_like_an_imported_game_takes_it_over(db, tmp_path, monkeypatch):
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    root = tmp_path / "games"
    root.mkdir()
    library = db_library_handler.add_library(Library(name="games", root_path=str(root)))
    imported = db_game_handler.add_game(
        Game(
            library_id=None, fs_name="Hollow Knight", name="Hollow Knight",
            imported=True, missing_from_fs=True,
        )
    )
    (root / "Hollow_Knight").mkdir()
    (root / "Hollow_Knight" / "setup.exe").write_bytes(b"x")
    (root / "Other").mkdir()
    (root / "Other" / "setup.exe").write_bytes(b"x")

    result = scan_library(library)

    taken = db_game_handler.get_game(imported.id)
    assert (taken.library_id, taken.fs_name, taken.imported, taken.missing_from_fs) == (
        library.id, "Hollow_Knight", False, False,
    )  # fmt: skip
    assert result.added == 2 and (imported.id, "Hollow_Knight") in result.new_games
    assert sorted(g.fs_name for g in db_game_handler.get_all_games()) == ["Hollow_Knight", "Other"]


def test_one_imported_game_is_taken_by_one_folder_only(db, tmp_path, monkeypatch):
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    root = tmp_path / "games"
    root.mkdir()
    library = db_library_handler.add_library(Library(name="games", root_path=str(root)))
    db_game_handler.add_game(
        Game(library_id=None, fs_name="Doom", name="Doom", imported=True, missing_from_fs=True)
    )
    for folder in ("Doom", "DOOM!"):
        (root / folder).mkdir()
        (root / folder / "setup.exe").write_bytes(b"x")

    scan_library(library)

    games = db_game_handler.get_all_games()
    assert len(games) == 2 and not any(g.imported for g in games)


def test_a_game_can_be_filed_under_a_library_and_still_has_no_files(api, tmp_path):
    library = db_library_handler.add_library(Library(name="Old games", root_path=str(tmp_path / "old")))

    body = api.post("/api/games/import", json={"name": "Celeste", "library_id": library.id}).json()

    assert (body["library_id"], body["imported"], body["missing_from_fs"]) == (library.id, True, True)
    assert api.get(f"/api/games/{body['id']}/files").json()["detail"] == {"code": "no_files"}
    game = db_game_handler.get_game(body["id"])
    assert game_sizes(game).total == 0
    with pytest.raises(HTTPException):
        fs_game_handler.get_game_root_abs_path(game)


def test_a_library_that_is_not_there_or_is_hidden_from_the_user_is_refused(api, db):
    hidden = db_library_handler.add_library(Library(name="Restricted", root_path="/library/restricted"))
    user = SimpleNamespace(id=1, is_admin=False, hidden_library_ids=[hidden.id])
    api.app.dependency_overrides[get_current_user] = lambda: user

    assert api.post("/api/games/import", json={"name": "A", "library_id": 99}).status_code == 404
    assert api.post("/api/games/import", json={"name": "A", "library_id": hidden.id}).status_code == 404
    assert api.get("/api/games").json() == []


def test_importing_again_files_an_entry_that_had_no_library(api, tmp_path):
    library = db_library_handler.add_library(Library(name="Old games", root_path=str(tmp_path / "old")))
    first = api.post("/api/games/import", json={"name": "Celeste"}).json()
    assert first["library_id"] is None

    again = api.post("/api/games/import", json={"name": "celeste", "library_id": library.id}).json()

    assert again["id"] == first["id"] and again["library_id"] == library.id


def test_a_scan_leaves_an_imported_game_filed_there_alone_and_does_not_count_it_missing(db, tmp_path):
    root = tmp_path / "games"
    root.mkdir()
    library = db_library_handler.add_library(Library(name="games", root_path=str(root)))
    kept = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Celeste", name="Celeste", imported=True, missing_from_fs=True)
    )

    result = scan_library(library)

    game = db_game_handler.get_game(kept.id)
    assert (game.imported, game.missing_from_fs, game.library_id) == (True, True, library.id)
    assert result.missing == 0


def test_a_folder_of_the_same_name_makes_a_filed_imported_game_a_normal_one(db, tmp_path, monkeypatch):
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    root = tmp_path / "games"
    (root / "Celeste").mkdir(parents=True)
    (root / "Celeste" / "setup.exe").write_bytes(b"x")
    library = db_library_handler.add_library(Library(name="games", root_path=str(root)))
    filed = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Celeste", name="Celeste", imported=True, missing_from_fs=True)
    )

    scan_library(library)

    game = db_game_handler.get_game(filed.id)
    assert (game.imported, game.missing_from_fs) == (False, False)
    assert len(db_game_handler.get_all_games()) == 1


def test_a_folder_named_a_little_differently_takes_a_filed_imported_game_over(db, tmp_path, monkeypatch):
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    root = tmp_path / "games"
    (root / "Hollow_Knight").mkdir(parents=True)
    (root / "Hollow_Knight" / "setup.exe").write_bytes(b"x")
    library = db_library_handler.add_library(Library(name="games", root_path=str(root)))
    filed = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Hollow Knight", name="Hollow Knight", imported=True, missing_from_fs=True)
    )

    result = scan_library(library)

    game = db_game_handler.get_game(filed.id)
    assert (game.fs_name, game.imported, game.missing_from_fs) == ("Hollow_Knight", False, False)
    assert len(db_game_handler.get_all_games()) == 1 and result.missing == 0


def test_deleting_a_library_keeps_the_games_imported_into_it(db, tmp_path):
    library = db_library_handler.add_library(Library(name="Old games", root_path=str(tmp_path / "old")))
    filed = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Celeste", name="Celeste", imported=True, missing_from_fs=True)
    )

    db_library_handler.delete_library(library.id)

    assert db_game_handler.get_game(filed.id).library_id is None


def test_a_library_is_made_with_a_name_and_a_folder_the_server_sees(db):
    from endpoints import libraries as libraries_endpoints

    app = FastAPI()
    app.include_router(libraries_endpoints.router, prefix="/api")
    admin = SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    client = TestClient(app)

    made = client.post("/api/libraries", json={"name": " Old games ", "root_path": "/games/old"})
    assert made.status_code == 201 and made.json()["name"] == "Old games"
    assert client.post("/api/libraries", json={"name": "Again", "root_path": "/games/old/"}).status_code == 409
    assert client.post("/api/libraries", json={"name": "Relative", "root_path": "games/new"}).status_code == 422
    assert client.post("/api/libraries", json={"name": " ", "root_path": "/games/new"}).status_code == 422


def _library_with(db, tmp_path, *names):
    root = tmp_path / "games"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(b"x")
    return db_library_handler.add_library(Library(name="games", root_path=str(root)))


def test_a_game_the_scrape_names_like_an_imported_one_becomes_that_game(db, tmp_path, monkeypatch):
    """Pocket Campfire: a client imported it, then the scan found `PocketCampfire_v2_WindowsInstaller.exe`, which is named like it only
    after the match. They stayed two entries (one with the saves, one with the installer) until the scrape merged them."""
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    monkeypatch.setattr(scrape_handler, "warm_videos", lambda games: None)
    library = _library_with(db, tmp_path, "PocketCampfire_v2_WindowsInstaller.exe")
    imported = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Pocket Campfire", name="Pocket Campfire", imported=True, missing_from_fs=True, sgdb_id=5)
    )
    scan_library(library)
    assert len(db_game_handler.get_all_games()) == 2  # the scan alone cannot tell

    def match(game):
        db_game_handler.update_game(game.id, {"name": "Pocket Campfire", "igdb_id": 77, "summary": "camp"})
        return True

    monkeypatch.setattr(scrape_handler, "scrape_game", match)
    scrape_handler.scrape_library(library.id)

    (game,) = db_game_handler.get_all_games()
    assert game.id == imported.id  # the saves and the clients' records are on this id
    assert (game.fs_name, game.library_id, game.imported, game.missing_from_fs) == ("PocketCampfire_v2_WindowsInstaller.exe", library.id, False, False)
    assert (game.igdb_id, game.summary, game.sgdb_id, game.size_bytes) == (77, "camp", 5, 1)  # what it lacked, from the other


def test_the_next_scan_also_joins_a_game_that_was_named_after_the_fact(db, tmp_path, monkeypatch):
    monkeypatch.setattr("handler.scan_handler.has_mods", lambda path: False)
    library = _library_with(db, tmp_path, "Setup_Game.exe")
    imported = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="Nice Game", name="Nice Game", imported=True, missing_from_fs=True)
    )
    scan_library(library)
    real = db_game_handler.get_game_by_fs_name(library.id, "Setup_Game.exe")
    db_game_handler.update_game(real.id, {"name": "Nice  game!"})  # renamed by hand, say

    scan_library(library)

    (game,) = db_game_handler.get_all_games()
    assert (game.id, game.fs_name) == (imported.id, "Setup_Game.exe")


def test_a_game_with_installs_or_saves_of_its_own_is_not_merged(db, tmp_path, monkeypatch):
    from handler.imports import adopt_placeholders

    library = _library_with(db, tmp_path, "Setup_Game.exe")
    db_game_handler.add_game(Game(library_id=None, fs_name="Nice Game", name="Nice Game", imported=True, missing_from_fs=True))
    real = db_game_handler.add_game(Game(library_id=library.id, fs_name="Setup_Game.exe", name="Nice Game"))
    monkeypatch.setattr(db_game_handler, "has_dependents", lambda game_id: True)

    assert adopt_placeholders(real.id) is None and len(db_game_handler.get_all_games()) == 2


def test_a_game_with_artwork_uploaded_under_its_id_is_not_merged(db, tmp_path):
    from handler.imports import adopt_placeholders

    library = _library_with(db, tmp_path, "Setup_Game.exe")
    db_game_handler.add_game(Game(library_id=None, fs_name="Nice Game", name="Nice Game", imported=True, missing_from_fs=True))
    real = db_game_handler.add_game(Game(library_id=library.id, fs_name="Setup_Game.exe", name="Nice Game"))
    db_game_handler.update_game(real.id, {"cover_path": f"/resources/media/{real.id}/cover.png"})

    assert adopt_placeholders(real.id) is None and len(db_game_handler.get_all_games()) == 2


def test_matching_a_game_again_by_name_drops_the_match_it_had(db, monkeypatch, tmp_path):
    """A game matched by another provider that is renamed is searched anew: the old source and ids must not stay, or the scrape
    would never ask IGDB or the other providers again (a game with a source is taken for matched)."""
    library = db_library_handler.add_library(Library(name="games", root_path=str(tmp_path)))
    game = db_game_handler.add_game(
        Game(library_id=library.id, fs_name="A", name="Old name", metadata_source="vndb", provider_ids={"vndb": "v17"})
    )
    seen = []
    monkeypatch.setattr(games_endpoints, "refresh_game", lambda g, keep=frozenset(), **kw: seen.append(g))
    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    admin = SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[])
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin

    response = TestClient(app).put(f"/api/games/{game.id}", json={"name": "New name"})

    assert response.status_code == 200
    assert seen[0].metadata_source is None and seen[0].provider_ids == {}
