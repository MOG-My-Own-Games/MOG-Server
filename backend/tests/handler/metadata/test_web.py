import httpx
import pytest

from handler.metadata import web


class Reply:
    def __init__(self, code=200, headers=None):
        self.status_code = code
        self.headers = headers or {}


@pytest.fixture
def net(monkeypatch):
    """httpx answers from a queue; time is a clock the sleeps move, so nothing really waits."""
    clock = {"now": 1000.0, "slept": []}
    answers, calls = [], []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fake_sleep(seconds):
        clock["slept"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(web.httpx, "request", fake_request)
    monkeypatch.setattr(web, "_sleep", fake_sleep)
    monkeypatch.setattr(web, "_now", lambda: clock["now"])
    web._next_slot.clear()
    web._cooldown.clear()
    return type("Net", (), {"answers": answers, "calls": calls, "clock": clock})


def test_requests_to_one_site_are_spaced_by_its_interval(net):
    net.answers += [Reply(), Reply(), Reply()]
    for _ in range(3):
        web.request("GET", "https://api.vndb.org/kana/vn")
    assert net.clock["slept"] == [pytest.approx(1.6), pytest.approx(1.6)]  # the first goes at once


def test_every_subdomain_of_itch_shares_one_pace_and_a_site_that_is_not_listed_is_not_paced(net):
    net.answers += [Reply(), Reply(), Reply(), Reply()]
    web.request("GET", "https://itch.io/search")
    web.request("GET", "https://ill.itch.io/portal-panic")
    assert net.clock["slept"] == [pytest.approx(1.0)]
    web.request("GET", "https://romm.example/api/roms")
    web.request("GET", "https://romm.example/api/roms")
    assert len(net.clock["slept"]) == 1


def test_a_missing_page_is_none_but_a_site_that_cannot_answer_is_not_a_missing_page(net):
    """A 429 used to read as "no results" and the game got a miss for a week."""
    net.answers += [Reply(404)]
    assert web.request("GET", "https://api.vndb.org/kana/vn") is None
    for code in (429, 403, 500, 503):
        web._cooldown.clear()
        net.answers.append(Reply(code))
        with pytest.raises(web.Unavailable):
            web.request("GET", "https://api.vndb.org/kana/vn")


def test_a_429_with_a_short_retry_after_is_waited_for_once(net):
    net.answers += [Reply(429, {"Retry-After": "7"}), Reply(200)]
    assert web.request("GET", "https://api.vndb.org/kana/vn").status_code == 200
    assert 7.0 in net.clock["slept"]

    web._cooldown.clear()
    net.answers += [Reply(429, {"Retry-After": "7"}), Reply(429, {"Retry-After": "7"})]
    with pytest.raises(web.Unavailable):
        web.request("GET", "https://api.vndb.org/kana/vn")


def test_a_site_that_refused_is_left_alone_for_a_while_then_asked_again(net):
    net.answers += [Reply(429)]
    with pytest.raises(web.Unavailable):
        web.request("GET", "https://store.steampowered.com/api/storesearch")
    calls = len(net.calls)

    with pytest.raises(web.Unavailable):
        web.request("GET", "https://store.steampowered.com/api/appdetails")
    assert len(net.calls) == calls  # not even sent

    net.clock["now"] += web.COOLDOWN_LIMITED + 1
    net.answers += [Reply(200)]
    assert web.request("GET", "https://store.steampowered.com/api/storesearch").status_code == 200


def test_an_unreachable_site_is_unavailable_and_the_persons_own_server_is_never_held_off(net):
    net.answers += [httpx.ConnectError("down")]
    with pytest.raises(web.Unavailable):
        web.request("GET", "https://www.dlsite.com/maniax/work")

    net.answers += [Reply(403), Reply(200)]
    with pytest.raises(web.Unavailable):
        web.request("GET", "https://romm.example/api/roms")
    assert web.request("GET", "https://romm.example/api/roms").status_code == 200  # fixed on their side: tried again at once
