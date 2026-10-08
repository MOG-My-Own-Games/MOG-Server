import asyncio
from types import SimpleNamespace

import httpx
import pytest
from endpoints import games
from fastapi import HTTPException
from handler import video_handler as vh

real_prefetch_in_background = vh.prefetch_in_background  # the conftest swaps the module's own out


@pytest.fixture(autouse=True)
def _fresh_cache():
    vh._searched.clear()
    yield
    vh._searched.clear()


def igdb(*pairs):
    return [{"name": name, "video_id": video_id} for name, video_id in pairs]


A, B, C, D = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd"


def test_igdb_videos_are_told_apart_by_their_names():
    found = vh.from_igdb(igdb(("Launch Trailer", A), ("Gameplay", B), ("Another trailer", C)))
    assert (found["intro"]["video_id"], found["gameplay"]["video_id"]) == (A, B)
    assert found["intro"]["source"] == "igdb"


def test_a_video_that_names_neither_stands_in_for_the_intro_only():
    found = vh.from_igdb(igdb(("Official video", A), ("Interview", B)))
    assert found["intro"]["video_id"] == A and "gameplay" not in found


def test_ids_that_are_not_youtube_ids_are_ignored():
    assert vh.from_igdb(igdb(("Trailer", "short"), ("Gameplay", ""))) == {}
    assert vh.from_igdb(None) == {}


def renderer(video_id, title, clock=None):
    label = (
        '"lengthText":{"accessibility":{"accessibilityData":{"label":"x"}},"simpleText":"' + str(clock) + '"}'
    )
    length = "," + label if clock else ""
    head = '{"videoRenderer":{"videoId":"' + video_id + '","thumbnail":{},'
    return head + '"title":{"runs":[{"text":"' + title + '"}]}' + length + "}}"


def R(video_id, title, seconds=None):
    return vh.Result(video_id, title, seconds)


def test_results_are_read_out_of_the_page_with_titles_and_lengths():
    page = renderer(A, "Little Big Adventure 2 \\u2013 Intro", "3:05") + renderer(
        B, 'Quote \\"x\\" here', "1:02:03"
    )
    page += renderer(C, "Live one")
    assert vh.results_in(page) == [
        R(A, "Little Big Adventure 2 \u2013 Intro", 185),
        R(B, 'Quote "x" here', 3723),
        R(C, "Live one", None),
    ]
    assert vh.results_in("<html>nothing</html>") == []


def test_a_result_has_to_be_about_the_game():
    results = [R(A, "Unrelated Game intro"), R(B, "Little Big Adventure 2 - Intro (1997)")]
    classic = "Twinsen's Little Big Adventure 2 Classic"
    assert vh._pick(classic, "intro", results) == results[1]
    odyssey = "Little Big Adventure 2: Twinsen's Odyssey"
    assert vh._pick(odyssey, "intro", results) == results[1]  # a subtitle is not needed
    assert vh._pick("Something Else Entirely", "intro", results) is None


def test_a_title_that_shares_only_part_of_the_name_is_not_taken():
    assert vh._pick("Jazz Jackrabbit 2", "intro", [R(A, "Jazz Jackrabbit trailer")]) is None  # no 2
    # The known limit of a short name: a sequel with the same words passes.
    assert vh._pick("Doom", "intro", [R(A, "Doom Eternal trailer")]) == R(A, "Doom Eternal trailer")


def test_an_intro_has_to_say_so_and_music_is_not_one():
    results = [
        R(A, "Jazz Jackrabbit Collection Soundtrack - Medley", 600),
        R(B, "Jazz Jackrabbit 2 OST", 240),
        R(C, "Jazz Jackrabbit main theme", 120),
        R(D, "Jazz Jackrabbit review", 300),
        R("eeeeeeeeeee", "Jazz Jackrabbit Intro", 95),
    ]
    assert vh._pick("Jazz Jackrabbit Collection", "intro", results) == results[-1]
    assert vh._pick("Jazz Jackrabbit Collection", "intro", results[:4]) is None


def test_music_and_compilations_are_not_intros_by_length_either():
    assert vh._pick("Doom", "intro", [R(A, "Doom intro 10 hours", 36000)]) is None
    assert vh._pick("Doom", "intro", [R(A, "Doom intro", 90)]) == R(A, "Doom intro", 90)


def test_gameplay_is_not_a_commented_or_foreign_or_lets_play_video():
    results = [
        R(A, "Jazz Jackrabbit 2 Gameplay ITA", 1500),
        R(B, "Jazz Jackrabbit 2 - Let's Play #1 gameplay", 1500),
        R(C, "Jazz Jackrabbit 2 gameplay con commento", 1500),
        R(
            D,
            "Jazz Jackrabbit 2 \u043f\u0440\u043e\u0445\u043e\u0436\u0434\u0435\u043d\u0438\u0435 gameplay",
            1500,
        ),
        R("eeeeeeeeeee", "Jazz Jackrabbit 2 gameplay with commentary", 1500),
        R("fffffffffff", "Jazz Jackrabbit 2 gameplay clip", 20),
        R("ggggggggggg", "Jazz Jackrabbit 2 gameplay", 1500),
    ]
    assert vh._pick("Jazz Jackrabbit 2", "gameplay", results) == results[-1]


def test_first_impressions_and_previews_are_commentary_too():
    results = [
        R(A, "Hearthlands | First Impressions Gameplay!", 900),
        R(B, "Hearthlands hands-on preview gameplay", 900),
    ]
    assert vh._pick("Hearthlands", "gameplay", results) is None


def test_a_video_that_says_it_has_no_commentary_is_welcome_and_comes_first():
    results = [
        R(A, "Jazz Jackrabbit 2 gameplay", 900),
        R(B, "Jazz Jackrabbit 2 gameplay (no commentary)", 900),
    ]
    assert vh._pick("Jazz Jackrabbit 2", "gameplay", results) == results[1]
    assert vh._pick("Jazz Jackrabbit 2", "gameplay", [R(A, "Jazz Jackrabbit 2 gameplay", 900)]) == R(
        A, "Jazz Jackrabbit 2 gameplay", 900
    )


def test_a_banned_word_in_the_games_own_name_does_not_ban_its_videos():
    assert vh._pick("Theme Hospital", "intro", [R(A, "Theme Hospital Intro", 120)]) == R(
        A, "Theme Hospital Intro", 120
    )
    assert vh._pick("Theme Hospital", "intro", [R(A, "Theme Hospital Intro Soundtrack", 120)]) is None


def test_igdb_videos_are_filtered_too():
    found = vh.from_igdb(igdb(("Original Soundtrack", A), ("Launch Trailer", B), ("Gameplay ITA", C)))
    assert found["intro"]["video_id"] == B and "gameplay" not in found


def test_missing_kinds_are_searched_and_the_order_is_intro_then_gameplay():
    asked = []

    def search(name, kind):
        asked.append(kind)
        return vh._entry(kind, {"intro": C, "gameplay": D}[kind], f"{name} {kind}", "youtube")

    videos = vh.find_videos(1, "Game", None, search)
    assert [v["kind"] for v in videos] == ["intro", "gameplay"] and asked == ["intro", "gameplay"]


def test_what_igdb_has_is_not_searched_for():
    asked = []

    def search(name, kind):
        asked.append(kind)
        return None

    videos = vh.find_videos(1, "Game", igdb(("Trailer", A), ("Gameplay", B)), search)
    assert [v["video_id"] for v in videos] == [A, B] and asked == []


def test_an_answer_is_remembered_for_a_while_a_miss_for_less_and_a_failure_not_at_all():
    clock = [0.0]
    calls = []

    def search(name, kind):
        calls.append(kind)
        return vh._entry(kind, C, "t", "youtube") if kind == "intro" else None

    vh.find_videos(1, "Game", None, search, lambda: clock[0])
    vh.find_videos(1, "Game", None, search, lambda: clock[0] + 60)
    assert calls == ["intro", "gameplay"]  # the second visit asked nothing
    vh.find_videos(1, "Game", None, search, lambda: clock[0] + vh.MISSED_TTL + 1)
    assert calls == ["intro", "gameplay", "gameplay"]  # the miss expired, the find did not

    def failing(name, kind):
        raise httpx.ConnectError("offline")

    assert vh.find_videos(2, "Other", None, failing) == []
    assert (2, "intro") not in vh._searched  # tried again next time


def test_the_endpoint_returns_the_videos_and_404s_for_an_unknown_game(monkeypatch):
    game = SimpleNamespace(id=7, name="Game", igdb_metadata={"videos": igdb(("Trailer", A))})
    monkeypatch.setattr(games.db_game_handler, "get_game", lambda _id: game if _id == 7 else None)
    monkeypatch.setattr(vh, "search_video", lambda name, kind: None)

    async def direct(fn, *args):
        return fn(*args)

    monkeypatch.setattr(games, "run_in_threadpool", direct)
    monkeypatch.setattr(vh, "find_videos", lambda *args: [vh._entry("intro", A, "Trailer", "igdb")])
    expected = {"videos": [vh._entry("intro", A, "Trailer", "igdb")]}
    assert asyncio.run(games.get_game_videos(SimpleNamespace(), 7)) == expected
    with pytest.raises(HTTPException) as err:
        asyncio.run(games.get_game_videos(SimpleNamespace(), 99))
    assert err.value.status_code == 404


def _games(*names):
    return [(i + 1, name, None) for i, name in enumerate(names)]


def test_a_prefetch_looks_games_up_and_pauses_only_after_those_that_went_to_youtube():
    asked, pauses = [], []

    def search(name, kind):
        asked.append((name, kind))
        return vh._entry(kind, C, "t", "youtube")

    games = [(1, "Alpha", None), (2, "Beta", igdb(("Trailer", A), ("Gameplay", B))), (3, "Gamma", None)]
    assert vh.prefetch(games, search, sleep=pauses.append, pause=2.0) == 2  # Beta had all from IGDB
    assert [n for n, _ in asked] == ["Alpha", "Alpha", "Gamma", "Gamma"] and pauses == [2.0, 2.0]

    asked.clear()
    pauses.clear()
    assert vh.prefetch(games, search, sleep=pauses.append) == 0  # remembered: nothing asked, nothing waited
    assert asked == [] and pauses == []


def test_a_game_that_cannot_be_looked_up_does_not_stop_the_rest():
    def search(name, kind):
        if name == "Alpha":
            raise httpx.ConnectError("offline")
        return vh._entry(kind, C, "t", "youtube")

    assert vh.prefetch(_games("Alpha", "Beta"), search, sleep=lambda s: None) == 1
    assert (2, "intro") in vh._searched and (1, "intro") not in vh._searched


def test_the_lookups_survive_a_restart():
    vh.find_videos(1, "Alpha", None, lambda n, k: vh._entry(k, C, "t", "youtube") if k == "intro" else None)
    assert vh.CACHE_FILE.is_file()

    vh._searched.clear()
    vh._cache_loaded = False  # a new process
    asked = []
    videos = vh.find_videos(1, "Alpha", None, lambda n, k: asked.append(k))
    assert asked == [] and [v["video_id"] for v in videos] == [C]  # the find and the miss both came back


def test_a_damaged_cache_file_is_ignored(tmp_path):
    vh.CACHE_FILE.write_text("not json{")
    vh._cache_loaded = False
    assert vh.find_videos(1, "Alpha", None, lambda n, k: None) == []
    vh.CACHE_FILE.write_text('{"x:intro": 3, "1:intro": [1.0, null], "2:bogus": [1.0, null]}')
    vh._searched.clear()
    vh._cache_loaded = False
    vh.find_videos(9, "Other", None, lambda n, k: None)
    assert (1, "intro") in vh._searched and (2, "bogus") not in vh._searched


def test_only_one_background_pass_runs_at_a_time(monkeypatch):
    import threading

    gate = threading.Event()
    monkeypatch.setattr(vh, "prefetch", lambda games: gate.wait(5) or 0)
    assert real_prefetch_in_background(_games("Alpha")) is True
    assert real_prefetch_in_background(_games("Beta")) is False  # left to the next scan
    gate.set()
    for _ in range(100):
        if vh._prefetching.acquire(blocking=False):
            vh._prefetching.release()
            break
        threading.Event().wait(0.02)
    assert real_prefetch_in_background(_games("Gamma")) is True
    gate.wait(0.1)
