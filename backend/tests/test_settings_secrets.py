import asyncio
from types import SimpleNamespace

from endpoints import settings
from endpoints.responses.settings import SettingsUpdateForm
from models.settings import Settings


def test_a_provider_key_is_never_shown_whole():
    assert settings.mask_secret("abcdefghijklmnop") == "••••mnop"
    assert settings.mask_secret("short") == "••••"
    assert settings.mask_secret(None) is None and settings.mask_secret("") == ""


def test_the_settings_answer_carries_only_masks(monkeypatch):
    row = Settings(igdb_client_id="id", igdb_client_secret="s" * 30, steamgriddb_api_key="k" * 30)
    shown = settings._schema(row)
    assert "s" * 8 not in shown.igdb_client_secret and "k" * 8 not in shown.steamgriddb_api_key
    assert shown.igdb_client_id == "id"


def test_sending_back_the_mask_keeps_the_saved_key_and_a_new_value_replaces_it(monkeypatch):
    row = Settings(igdb_client_secret="s" * 30, steamgriddb_api_key="k" * 30)
    saved = []
    monkeypatch.setattr(settings.db_settings_handler, "get_settings", lambda: row)
    monkeypatch.setattr(settings.db_settings_handler, "update_settings", lambda changes: saved.append(dict(changes)) or row)
    admin = SimpleNamespace(is_admin=True)

    asyncio.run(
        settings.update_settings(
            admin,
            SettingsUpdateForm(igdb_client_secret=settings.mask_secret("s" * 30), steamgriddb_api_key="a-new-key-value"),
        )
    )

    assert saved == [{"steamgriddb_api_key": "a-new-key-value"}]
