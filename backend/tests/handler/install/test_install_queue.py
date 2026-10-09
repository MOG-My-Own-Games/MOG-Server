"""More installs than the server runs at once wait in a queue, in the order they asked, and never get an error."""

import pytest

from handler.database import db_install_session_handler as sessions
from handler.install import runner
from models.install_session import InstallSession, InstallSessionState

S = InstallSessionState


@pytest.fixture
def queue(db, monkeypatch):
    started = []
    monkeypatch.setattr(runner, "enqueue_install", started.append)
    monkeypatch.setattr(runner, "INSTALL_MAX_CONCURRENCY", 1)

    def add(state=S.DETECTING):
        return sessions.add_session(InstallSession(game_id=1, user_id=1, state=state)).id

    return type("Q", (), {"started": started, "add": staticmethod(add)})


def state(session_id):
    return sessions.get_session(session_id).state


def test_with_room_a_session_starts_at_once(queue):
    first = queue.add()
    assert runner.start_or_queue(first) is True
    assert state(first) == S.INSTALLING and queue.started == [first]


def test_with_every_place_taken_a_session_waits_instead_of_failing(queue):
    first, second, third = queue.add(), queue.add(), queue.add()
    runner.start_or_queue(first)

    assert runner.start_or_queue(second) is False and runner.start_or_queue(third) is False

    assert state(second) == S.QUEUED and state(third) == S.QUEUED and queue.started == [first]
    assert sessions.queue_position(second) == 1 and sessions.queue_position(third) == 2
    assert sessions.queue_position(first) is None


def test_a_place_that_frees_up_goes_to_the_one_that_waited_longest(queue):
    first, second, third = queue.add(), queue.add(), queue.add()
    for sid in (first, second, third):
        runner.start_or_queue(sid)
    assert runner.dispatch_queue() == 0  # nobody left: still full

    sessions.update_session(first, {"state": S.DONE})
    assert runner.dispatch_queue() == 1
    assert state(second) == S.INSTALLING and state(third) == S.QUEUED and queue.started == [first, second]

    sessions.update_session(second, {"state": S.FAILED})
    assert runner.dispatch_queue() == 1 and state(third) == S.INSTALLING


def test_a_run_that_ends_starts_the_next_one_by_itself(queue, monkeypatch):
    first, second = queue.add(), queue.add()
    runner.start_or_queue(first)
    runner.start_or_queue(second)

    def finish(session_id, attempt=1):
        sessions.update_session(session_id, {"state": S.DONE})
        return False

    monkeypatch.setattr(runner, "_run_install", finish)
    runner.run_install(first)

    assert state(second) == S.INSTALLING and queue.started == [first, second]


def test_more_places_run_more_at_a_time(queue, monkeypatch):
    monkeypatch.setattr(runner, "INSTALL_MAX_CONCURRENCY", 2)
    ids = [queue.add() for _ in range(3)]
    results = [runner.start_or_queue(i) for i in ids]
    assert results == [True, True, False] and state(ids[2]) == S.QUEUED


def test_a_queued_session_is_active_and_never_expires_before_it_runs(queue):
    from models.install_session import ACTIVE_INSTALL_STATES, RUNNING_INSTALL_STATES

    assert S.QUEUED in ACTIVE_INSTALL_STATES and S.QUEUED not in RUNNING_INSTALL_STATES
    waiting = queue.add(S.QUEUED)
    from datetime import datetime, timedelta, timezone

    sessions.update_session(waiting, {"expires_at": datetime.now(timezone.utc) - timedelta(days=1)})
    assert waiting not in [s.id for s in sessions.get_expired_sessions()]


# --- the endpoint: no 429, the answer says where the install stands ---

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from endpoints import install as install_endpoint  # noqa: E402
from endpoints.responses.install import InstallStartForm  # noqa: E402


@pytest.fixture
def endpoint(queue, monkeypatch):
    monkeypatch.setattr(install_endpoint.db_game_handler, "get_game", lambda gid: SimpleNamespace(id=gid, name="G"))
    monkeypatch.setattr(install_endpoint, "_archive_needs_no_installer", lambda *a: asyncio.sleep(0, False))
    monkeypatch.setattr(install_endpoint, "default_manual_mode", lambda: False)
    monkeypatch.setattr(install_endpoint, "default_auto_mode", lambda: False)
    monkeypatch.setattr(install_endpoint, "purge_superseded_sessions", lambda *a, **k: 0)
    monkeypatch.setattr(runner, "dispatch_queue", lambda: 0)
    user = SimpleNamespace(id=1)

    def start(game_id):
        return asyncio.run(install_endpoint.start_install_session(user, game_id, InstallStartForm(installer_path="setup.exe")))

    return SimpleNamespace(start=start, user=user, started=queue.started)


def test_a_second_install_is_queued_and_the_answer_says_so(endpoint):
    first = endpoint.start(1)
    second = endpoint.start(2)

    assert first.state == S.INSTALLING and first.queue_position is None
    assert second.state == S.QUEUED and second.queue_position == 1
    assert endpoint.started == [first.id]


def test_asking_again_for_a_queued_game_returns_the_same_queued_session(endpoint):
    endpoint.start(1)
    queued = endpoint.start(2)
    again = endpoint.start(2)
    assert again.id == queued.id and again.state == S.QUEUED


def test_cancelling_a_queued_install_takes_it_out_of_the_queue(endpoint, monkeypatch):
    endpoint.start(1)
    queued = endpoint.start(2)
    monkeypatch.setattr(install_endpoint, "dispatch_queue", lambda: 0)

    cancelled = asyncio.run(install_endpoint.cancel_install(endpoint.user, 2))

    assert cancelled.state == S.FAILED and cancelled.error == "Cancelled"
    assert sessions.queue_position(queued.id) is None


# --- the list of active installs: those running first, the queued ones below; a restarted session counts from zero ---


def test_active_installs_list_the_running_ones_first_and_the_queued_below_in_order(endpoint, monkeypatch):
    first = endpoint.start(1)
    second = endpoint.start(2)
    third = endpoint.start(3)
    monkeypatch.setattr(install_endpoint.db_install_session_handler, "get_dashboard_sessions_for_user", lambda uid: [
        sessions.get_session(third.id), sessions.get_session(second.id), sessions.get_session(first.id)
    ])  # the database's own order puts the newest first

    listed = asyncio.run(install_endpoint.get_active_installs(endpoint.user))

    assert [(s.id, s.state, s.queue_position) for s in listed] == [
        (first.id, S.INSTALLING, None),
        (second.id, S.QUEUED, 1),
        (third.id, S.QUEUED, 2),
    ]


def test_a_session_started_again_does_not_keep_the_counts_of_its_earlier_run(endpoint, monkeypatch):
    done = sessions.add_session(InstallSession(game_id=9, user_id=1, state=S.DONE, bytes_written=451_426, bytes_total=451_426))
    monkeypatch.setattr(install_endpoint, "_pick_reusable_session", lambda own: sessions.get_session(done.id))  # its cache is reused

    again = endpoint.start(9)

    assert again.id == done.id and (again.bytes_written, again.bytes_total) == (0, 0)
