import httpx
from handler.metadata import sgdb_handler


def _answer(monkeypatch, response: httpx.Response) -> None:
    monkeypatch.setattr(sgdb_handler, "_api_key", lambda: "key")
    monkeypatch.setattr(sgdb_handler.httpx, "get", lambda *a, **k: response)


def test_an_answer_that_is_not_json_is_a_failed_search_not_a_crash(monkeypatch):
    """Regression test: SteamGridDB answered 200 with an HTML page, `resp.json()` raised a JSONDecodeError nothing caught,
    and applying a match, saving a game and scraping all ended in a 500."""
    request = httpx.Request("GET", "https://www.steamgriddb.com/api/v2/x")
    _answer(monkeypatch, httpx.Response(200, text="<html>blocked</html>", request=request))
    assert sgdb_handler.search_games("One.") == []
    assert sgdb_handler.get_grids(1) == []
    assert sgdb_handler.get_media(1) == {}


def test_a_good_answer_is_still_read(monkeypatch):
    request = httpx.Request("GET", "https://www.steamgriddb.com/api/v2/x")
    _answer(monkeypatch, httpx.Response(200, json={"data": [{"id": 7, "name": "One."}]}, request=request))
    assert sgdb_handler.search_games("One.") == [{"id": 7, "name": "One."}]
