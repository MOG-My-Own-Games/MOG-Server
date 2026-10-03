from types import SimpleNamespace

import pytest
from handler.install import defaults


def _settings(monkeypatch, **fields):
    import handler.database as db

    row = SimpleNamespace(install_default_auto_mode=None, install_default_manual_mode=None, **fields)
    monkeypatch.setattr(db.db_settings_handler, "get_settings", lambda: row)
    return row


@pytest.mark.parametrize(
    "configured,env,expected",
    [(True, False, True), (False, True, False), (None, True, True), (None, False, False)],
)
def test_default_auto_mode_prefers_db_setting(monkeypatch, configured, env, expected):
    row = _settings(monkeypatch)
    row.install_default_auto_mode = configured
    monkeypatch.setattr(defaults, "INSTALL_AUTO_MODE_DEFAULT", env)
    assert defaults.default_auto_mode() is expected


@pytest.mark.parametrize("configured,expected", [(True, True), (False, False), (None, False)])
def test_default_manual_mode(monkeypatch, configured, expected):
    row = _settings(monkeypatch)
    row.install_default_manual_mode = configured
    assert defaults.default_manual_mode() is expected


@pytest.mark.parametrize("configured,env,expected", [(8, 4, 8), (None, 6, 6), (0, 4, 1), (99, 4, 16)])
def test_download_workers_prefers_db_setting_and_stays_in_range(monkeypatch, configured, env, expected):
    row = _settings(monkeypatch, download_workers=configured)
    monkeypatch.setattr(defaults, "INSTALL_DOWNLOAD_WORKERS_DEFAULT", env)
    assert defaults.default_download_workers() == expected
