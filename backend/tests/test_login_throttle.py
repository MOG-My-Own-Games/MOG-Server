from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from handler import auth


def _request(host="10.0.0.5"):
    return SimpleNamespace(client=SimpleNamespace(host=host))


def _login(username, password, host="10.0.0.5"):
    return auth.get_current_user(_request(host), HTTPBasicCredentials(username=username, password=password))


@pytest.fixture(autouse=True)
def _users(monkeypatch):
    auth._failures.clear()
    auth._verified.clear()
    ann = SimpleNamespace(id=1, username="ann", enabled=True, hashed_password=auth.hash_password("right-password"))
    monkeypatch.setattr(auth.db_user_handler, "get_user_by_username", lambda name: ann if name == "ann" else None)
    return ann


def _status(username, password, host="10.0.0.5"):
    with pytest.raises(HTTPException) as refused:
        _login(username, password, host)
    return refused.value.status_code


def test_after_a_few_wrong_tries_even_the_right_password_waits():
    assert [_status("ann", "wrong") for _ in range(auth.FAILURES_FREE)] == [401] * auth.FAILURES_FREE
    assert _status("ann", "right-password") == 429


def test_the_wait_ends_and_the_right_password_clears_the_count(monkeypatch):
    for _ in range(auth.FAILURES_FREE):
        _status("ann", "wrong")
    now = auth.time.monotonic()
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + 10)
    assert _login("ann", "right-password").username == "ann"
    assert auth._failures == {}


def test_another_address_is_not_locked_out_by_someone_elses_guesses():
    for _ in range(auth.FAILURES_FREE):
        _status("ann", "wrong", host="10.0.0.9")
    assert _login("ann", "right-password", host="10.0.0.5").username == "ann"


def test_an_unknown_name_is_counted_and_checked_like_a_wrong_password(monkeypatch):
    checked = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: checked.append(h) or real(p, h))
    assert _status("nobody", "x") == 401
    assert checked == [auth._DUMMY_HASH]
    assert auth._failures


def test_a_rejected_login_carries_no_basic_challenge_so_a_browser_opens_no_box_of_its_own():
    """The web UI sends the credentials itself; a WWW-Authenticate on the 401 made the browser add a second sign-in box."""
    with pytest.raises(HTTPException) as refused:
        _login("ann", "wrong")
    assert refused.value.status_code == 401
    assert not refused.value.headers
