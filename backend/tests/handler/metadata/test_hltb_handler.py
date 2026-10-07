import json

import httpx
import pytest

import config
from handler.metadata import hltb_handler
from handler.metadata.hltb_handler import HLTBUnavailable
from utils.hltb_search import HLTB_SEARCH_URL

SESSION = {"token": "tok", "hpKey": "k", "hpVal": "v"}
DOOM = {
    "game_id": 7,
    "game_name": "Doom",
    "comp_main": 36000,
    "comp_plus": 54000,
    "comp_100": 90000,
    "comp_all": 50000,
    "comp_main_count": 120,
    "release_world": 1993,
    "review_score": 90,
}


class FakeSite:
    """Stands in for httpx's module-level get/post; `reply(method, url, kwargs)` answers (status, body)."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, body = self.reply(method, url, kwargs)
        request = httpx.Request(method, url)
        if isinstance(body, str):
            return httpx.Response(status, text=body, request=request)
        return httpx.Response(status, json=body, request=request)

    def urls(self, method=None):
        return [url for m, url, _ in self.calls if method in (None, m)]


@pytest.fixture(autouse=True)
def _fresh_handler(monkeypatch):
    monkeypatch.setattr(config, "HLTB_ENABLED", True)
    monkeypatch.setattr(hltb_handler, "_search_url", HLTB_SEARCH_URL)
    monkeypatch.setattr(hltb_handler, "_session", None)
    for name in ("_session_retry_after", "_rediscovery_retry_after", "_next_request_at"):
        monkeypatch.setattr(hltb_handler, name, 0.0)
    monkeypatch.setattr(hltb_handler.time, "sleep", lambda seconds: None)


def serve(monkeypatch, reply) -> FakeSite:
    site = FakeSite(reply)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: site.request("GET", url, **kw))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: site.request("POST", url, **kw))
    return site


def healthy(games):
    def reply(method, url, kwargs):
        return (200, SESSION) if url.endswith("/init") else (200, {"data": games})

    return reply


class TestExtractMetadata:
    def test_times_are_kept_in_seconds_with_their_counts(self):
        meta = hltb_handler.extract_metadata(DOOM)
        assert meta["main_story"] == 36000 and meta["main_plus_extra"] == 54000
        assert meta["completionist"] == 90000 and meta["all_styles"] == 50000
        assert meta["main_story_count"] == 120 and "main_plus_extra_count" not in meta
        assert meta["release_year"] == 1993 and meta["review_score"] == 90

    def test_zero_and_missing_values_are_left_out(self):
        assert hltb_handler.extract_metadata({"game_id": 1, "comp_main": 0, "comp_plus": 7200}) == {"main_plus_extra": 7200}

    def test_the_game_page_gives_the_release_as_a_date(self):
        assert hltb_handler.extract_metadata({"comp_main": 60, "release_world": "2004-11-16"})["release_year"] == 2004

    def test_a_game_with_no_time_has_none(self):
        assert not hltb_handler.has_times(hltb_handler.extract_metadata({"game_id": 1, "review_score": 80}))


class TestSearchGames:
    def test_returns_games_with_a_time_and_skips_the_rest(self, monkeypatch):
        site = serve(monkeypatch, healthy([DOOM, {"game_id": 8, "game_name": "Untimed", "comp_main": 0}]))
        found = hltb_handler.search_games("doom")
        assert [g["id"] for g in found] == [7] and found[0]["name"] == "Doom"
        assert found[0]["metadata"]["main_story"] == 36000
        _, _, kwargs = site.calls[-1]
        assert kwargs["headers"]["x-auth-token"] == "tok" and kwargs["json"]["k"] == "v"

    def test_the_session_is_minted_once_for_several_searches(self, monkeypatch):
        site = serve(monkeypatch, healthy([DOOM]))
        hltb_handler.search_games("doom")
        hltb_handler.search_games("quake")
        assert len([u for u in site.urls("GET") if u.endswith("/init")]) == 1

    def test_switched_off_asks_nothing(self, monkeypatch):
        monkeypatch.setattr(config, "HLTB_ENABLED", False)
        site = serve(monkeypatch, healthy([DOOM]))
        assert hltb_handler.search_games("doom") == [] and hltb_handler.get_game_by_id(7) is None
        assert site.calls == []

    def test_a_rejected_session_is_renewed_and_the_search_retried(self, monkeypatch):
        state = {"searches": 0}

        def reply(method, url, kwargs):
            if url.endswith("/init"):
                return 200, SESSION
            state["searches"] += 1
            return (403, {}) if state["searches"] == 1 else (200, {"data": [DOOM]})

        site = serve(monkeypatch, reply)
        assert [g["id"] for g in hltb_handler.search_games("doom")] == [7]
        assert len([u for u in site.urls("GET") if u.endswith("/init")]) == 2

    def test_a_throttled_search_is_waited_out_and_retried(self, monkeypatch):
        waits = []
        monkeypatch.setattr(hltb_handler.time, "sleep", waits.append)
        state = {"searches": 0}

        def reply(method, url, kwargs):
            if url.endswith("/init"):
                return 200, SESSION
            state["searches"] += 1
            return (429, {}) if state["searches"] == 1 else (200, {"data": [DOOM]})

        serve(monkeypatch, reply)
        assert hltb_handler.search_games("doom")
        assert hltb_handler.RATE_LIMIT_BACKOFF_SECONDS in waits

    def test_a_search_that_keeps_failing_is_unavailable(self, monkeypatch):
        serve(monkeypatch, lambda m, u, k: (200, SESSION) if u.endswith("/init") else (500, {}))
        with pytest.raises(HLTBUnavailable):
            hltb_handler.search_games("doom")

    def test_a_failed_mint_is_not_tried_again_at_once(self, monkeypatch):
        site = serve(monkeypatch, lambda m, u, k: (403, {}))
        for _ in range(2):
            with pytest.raises(HLTBUnavailable):
                hltb_handler.search_games("doom")
        assert len(site.calls) == 1

    def test_an_answer_without_a_result_list_is_unavailable(self, monkeypatch):
        serve(monkeypatch, lambda m, u, k: (200, SESSION) if u.endswith("/init") else (200, {"oops": 1}))
        with pytest.raises(HLTBUnavailable):
            hltb_handler.search_games("doom")

    def test_a_moved_search_route_is_found_again_on_a_404(self, monkeypatch):
        new_url = "https://howlongtobeat.com/api/lookup/abc"
        monkeypatch.setattr(hltb_handler, "discover_endpoint", lambda: new_url)

        def reply(method, url, kwargs):
            if not url.startswith(new_url):
                return 404, {}
            return (200, SESSION) if url.endswith("/init") else (200, {"data": [DOOM]})

        site = serve(monkeypatch, reply)
        assert [g["id"] for g in hltb_handler.search_games("doom")] == [7]
        assert site.urls("POST") == [new_url]


class TestGetGameById:
    @staticmethod
    def page(game):
        return '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"game":{"data":{"game":%s}}}}}</script></html>' % (
            json.dumps(game)
        )

    def test_reads_the_record_from_the_game_page(self, monkeypatch):
        site = serve(monkeypatch, lambda m, u, k: (200, self.page([{**DOOM, "release_world": "1993-12-10"}])))
        found = hltb_handler.get_game_by_id(7)
        assert found["id"] == 7 and found["metadata"]["completionist"] == 90000 and found["metadata"]["release_year"] == 1993
        assert site.urls() == ["https://howlongtobeat.com/game/7"]

    def test_a_missing_game_is_none(self, monkeypatch):
        serve(monkeypatch, lambda m, u, k: (404, {}))
        assert hltb_handler.get_game_by_id(7) is None

    def test_an_empty_record_list_is_none(self, monkeypatch):
        serve(monkeypatch, lambda m, u, k: (200, self.page([])))
        assert hltb_handler.get_game_by_id(7) is None

    def test_a_page_without_the_payload_is_unavailable(self, monkeypatch):
        serve(monkeypatch, lambda m, u, k: (200, "<html></html>"))
        with pytest.raises(HLTBUnavailable):
            hltb_handler.get_game_by_id(7)
