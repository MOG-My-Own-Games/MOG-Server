"""The providers a game is matched with: each can be taken away (DELETE /metadata/{provider}) and chosen again."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from handler import scrape_handler
from models.game import Game


def _game(**over) -> Game:
    base = dict(id=1, library_id=1, fs_name="G", name="G", igdb_id=None, provider_ids={}, provider_misses={}, igdb_metadata={}, summary=None)
    return Game(**{**base, **over})


def _in_the_future(misses: dict, key: str) -> bool:
    return datetime.fromisoformat(misses[key]) > datetime.now(UTC) + timedelta(days=365)


def test_a_dismissed_provider_is_told_from_one_that_only_missed():
    now = datetime.now(UTC).isoformat()
    assert scrape_handler._missed(_game(provider_misses={"vndb": now}), "vndb")
    assert not scrape_handler._dismissed(_game(provider_misses={"vndb": now}), "vndb")  # a scrape a person asks for asks it again


def test_when_the_source_is_removed_the_next_provider_by_priority_gives_the_metadata(monkeypatch):
    game = _game(provider_ids={"itch": "a.b", "vndb": "v17"}, metadata_source=None, summary=None)
    monkeypatch.setattr(scrape_handler.providers, "by_key", lambda key: SimpleNamespace(get=lambda ref: {"id": ref, "summary": f"from {key}"}))
    assert scrape_handler.promote_source(game) == {"metadata_source": "vndb", "igdb_metadata": {"id": "v17", "summary": "from vndb"}, "summary": "from vndb"}
    assert scrape_handler.promote_source(_game(provider_ids={"vndb": "v17"}, metadata_source="vndb")) == {}  # it has one
    assert scrape_handler.promote_source(_game(provider_ids={})) == {}


def test_the_priority_the_model_names_titles_by_is_the_one_scrapes_take_metadata_by():
    from models.game import PROVIDER_PRIORITY

    assert PROVIDER_PRIORITY == scrape_handler.PRIORITY


def test_removing_the_igdb_match_takes_its_id_metadata_and_summary_and_the_automatic_pass_leaves_it_alone():
    record = {"id": 5, "summary": "From IGDB."}
    game = _game(igdb_id=5, provider_ids={"igdb": 5}, metadata_source="igdb", igdb_metadata=record, summary="From IGDB.")

    update = scrape_handler.remove_match(game, "igdb")

    assert update["igdb_id"] is None and update["provider_ids"] == {}
    assert update["metadata_source"] is None and update["igdb_metadata"] == {} and update["summary"] is None
    assert _in_the_future(update["provider_misses"], "igdb")
    assert scrape_handler._missed(_game(provider_misses=update["provider_misses"]), "igdb")


def test_a_summary_the_person_wrote_stays_when_the_match_goes():
    game = _game(provider_ids={"vndb": "v17"}, metadata_source="vndb", igdb_metadata={"summary": "VNDB's"}, summary="Mine.")
    update = scrape_handler.remove_match(game, "vndb")
    assert "summary" not in update and update["metadata_source"] is None


def test_removing_the_source_while_another_provider_is_chosen_for_the_metadata_keeps_that_one():
    game = _game(igdb_id=5, provider_ids={"igdb": 5, "itch": "a.b"}, metadata_source="itch", igdb_metadata={"id": "a.b"})
    update = scrape_handler.remove_match(game, "igdb")
    assert update["igdb_id"] is None and update["provider_ids"] == {"itch": "a.b"} and "metadata_source" not in update


def test_removing_a_provider_that_is_not_the_source_keeps_the_metadata_of_the_one_that_is():
    game = _game(igdb_id=5, provider_ids={"igdb": 5, "itch": "a.b"}, metadata_source="igdb", igdb_metadata={"id": 5})
    update = scrape_handler.remove_match(game, "itch")
    assert update["provider_ids"] == {"igdb": 5} and "metadata_source" not in update and "igdb_metadata" not in update


def test_removing_steamgriddb_and_how_long_to_beat():
    assert scrape_handler.remove_match(_game(sgdb_id=7), "sgdb")["sgdb_id"] is None
    hltb = scrape_handler.remove_match(_game(hltb_id=9, hltb_metadata={"main_story": 1}), "hltb")
    assert hltb["hltb_id"] == 0 and hltb["hltb_metadata"] == {}  # 0 is "looked up, nothing": a scan does not search again
    assert scrape_handler._dismissed(_game(provider_misses=hltb["provider_misses"]), "hltb")  # and a scrape does not either


@pytest.fixture
def api(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from endpoints import games as games_endpoints
    from handler.auth import get_current_user
    from handler.database import db_game_handler, db_library_handler, db_user_handler
    from models.library import Library
    from models.user import Role, User

    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    app.dependency_overrides[get_current_user] = lambda: admin
    lib = db_library_handler.add_library(Library(name="L", root_path="/x"))
    add = lambda **kw: db_game_handler.add_game(Game(library_id=lib.id, fs_name="g", name="g", **kw))  # noqa: E731
    return SimpleNamespace(client=TestClient(app), add=add, get=db_game_handler.get_game)


def test_fetching_one_provider_reads_that_provider_only_and_makes_it_the_metadata(api, monkeypatch):
    from handler.metadata import itch_handler, steam_handler

    game = api.add(provider_ids={"itch": "dev.game", "steam": 7}, metadata_source="steam", igdb_metadata={"id": 7})
    record = {"id": "dev.game", "name": "Dev Game", "summary": "From itch.", "cover": None}
    monkeypatch.setattr(itch_handler, "get", lambda ref: record if ref == "dev.game" else None)
    monkeypatch.setattr(steam_handler, "get", lambda ref: pytest.fail("the other providers are not asked"))
    monkeypatch.setattr(scrape_handler, "_store_media", lambda *a, **k: False)

    response = api.client.post(f"/api/games/{game.id}/fetch-metadata/itch")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata_source"] == "itch" and body["name"] == "Dev Game" and body["summary"] == "From itch."
    assert body["provider_ids"] == {"itch": "dev.game", "steam": 7}


def test_fetching_a_provider_the_game_is_not_matched_with_is_a_conflict_and_an_unknown_one_is_not_found(api):
    game = api.add()
    assert api.client.post(f"/api/games/{game.id}/fetch-metadata/itch").status_code == 409
    assert api.client.post(f"/api/games/{game.id}/fetch-metadata/nope").status_code == 404


def test_a_provider_that_cannot_answer_is_a_503_and_changes_nothing(api, monkeypatch):
    from handler.metadata import itch_handler, web

    game = api.add(provider_ids={"itch": "dev.game"}, metadata_source="itch", igdb_metadata={"id": "x"})
    monkeypatch.setattr(itch_handler, "get", lambda ref: (_ for _ in ()).throw(web.Unavailable("429")))
    assert api.client.post(f"/api/games/{game.id}/fetch-metadata/itch").status_code == 503
    assert api.get(game.id).igdb_metadata == {"id": "x"}


def test_fetching_how_long_to_beat_reads_the_times_again(api, monkeypatch):
    from handler.metadata import hltb_handler

    game = api.add(hltb_id=9, hltb_metadata={"main_story": 1})
    monkeypatch.setattr(hltb_handler, "get_game_by_id", lambda i: {"id": i, "name": "G", "metadata": {"main_story": 3600}})
    assert api.client.post(f"/api/games/{game.id}/fetch-metadata/hltb").status_code == 200
    assert api.get(game.id).hltb_metadata == {"main_story": 3600}
