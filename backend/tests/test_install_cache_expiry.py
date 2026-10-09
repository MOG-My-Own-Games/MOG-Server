import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from endpoints import install, settings
from endpoints.responses.settings import SettingsUpdateForm
from handler.database import db_install_session_handler, db_settings_handler
from models.install_session import InstallSession, InstallSessionState

DAY = timedelta(days=1)


def _session(game_id, expires_at=None, state=InstallSessionState.DONE):
    return db_install_session_handler.add_session(
        InstallSession(game_id=game_id, user_id=1, state=state, expires_at=expires_at)
    )


def _expiry(session_id):
    return db_install_session_handler.get_session(session_id).expires_at


def _set_ttl(days):
    asyncio.run(settings.update_settings(SimpleNamespace(), SettingsUpdateForm(install_cache_ttl_days=days)))


@pytest.fixture
def seeded(db):
    """SQLite does not enforce the foreign keys, so the sessions need no games or users behind them."""


def test_a_positive_ttl_dates_the_caches_that_never_expired(seeded):
    never = _session(1)
    dated = _session(2, datetime.now(timezone.utc) + 3 * DAY)

    _set_ttl(14)

    assert _expiry(never.id) is not None
    assert abs(_expiry(never.id).replace(tzinfo=timezone.utc) - (datetime.now(timezone.utc) + 14 * DAY)) < timedelta(minutes=1)
    assert _expiry(dated.id).replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) + 4 * DAY


def test_a_ttl_of_zero_leaves_every_expiry_as_it_is(seeded):
    dated = _session(1, datetime.now(timezone.utc) + 3 * DAY)
    before = _expiry(dated.id)

    _set_ttl(0)

    assert _expiry(dated.id) == before


def test_reset_takes_the_ttl_in_force_now(seeded):
    dated = _session(1, datetime.now(timezone.utc) + 3 * DAY)

    _set_ttl(0)
    result = asyncio.run(install.reset_install_cache_expiry(SimpleNamespace(), dated.id))
    assert result.expires_at is None and _expiry(dated.id) is None

    db_settings_handler.update_settings({"install_cache_ttl_days": 2})
    result = asyncio.run(install.reset_install_cache_expiry(SimpleNamespace(), dated.id))
    assert abs(result.expires_at - (datetime.now(timezone.utc) + 2 * DAY)) < timedelta(minutes=1)


def test_reset_of_an_unknown_or_evicted_cache_is_a_404(seeded):
    evicted = _session(1, state=InstallSessionState.EXPIRED)

    for session_id in (999, evicted.id):
        with pytest.raises(HTTPException) as err:
            asyncio.run(install.reset_install_cache_expiry(SimpleNamespace(), session_id))
        assert err.value.status_code == 404


def test_the_listing_carries_each_expiry(seeded, monkeypatch, tmp_path):
    when = datetime.now(timezone.utc) + 5 * DAY
    kept, never = _session(1, when), _session(2)
    folders = []
    for s in (kept, never):
        folder = tmp_path / str(s.id)
        folder.mkdir()
        folders.append(folder)
    monkeypatch.setattr(install, "cache_root_dirs", lambda: folders)

    entries = asyncio.run(install.list_install_cache(SimpleNamespace())).entries

    assert entries[0].expires_at.tzinfo is not None
    assert entries[1].expires_at is None
