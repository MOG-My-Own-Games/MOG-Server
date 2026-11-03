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


def test_the_saves_kept_per_device_default_to_the_environments_and_stay_within_their_range():
    assert settings._schema(Settings()).saves_keep_versions == 3
    assert settings._schema(Settings(saves_keep_versions=8)).saves_keep_versions == 8
    for bad in (0, 21):
        try:
            SettingsUpdateForm(saves_keep_versions=bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


TOKEN = "rmm_" + "cd" * 32


def _put(monkeypatch, row, **form):
    saved = []
    monkeypatch.setattr(settings.db_settings_handler, "get_settings", lambda: row)
    monkeypatch.setattr(settings.db_settings_handler, "update_settings", lambda changes: saved.append(dict(changes)) or row)
    asyncio.run(settings.update_settings(SimpleNamespace(is_admin=True), SettingsUpdateForm(**form)))
    return saved


def test_a_romm_pairing_code_is_saved_as_the_token_it_is_exchanged_for(monkeypatch):
    asked = []
    monkeypatch.setattr(settings.romm_handler, "exchange", lambda address, code: asked.append((address, code)) or TOKEN)

    saved = _put(monkeypatch, Settings(romm_url="https://romm.example"), romm_token="1234-5678")

    assert asked == [("https://romm.example", "1234-5678")]
    assert saved == [{"romm_token": TOKEN}]


def test_a_romm_token_is_kept_as_typed_and_never_exchanged(monkeypatch):
    monkeypatch.setattr(settings.romm_handler, "exchange", lambda *a: (_ for _ in ()).throw(AssertionError("exchanged")))
    assert _put(monkeypatch, Settings(), romm_token=f" {TOKEN} ") == [{"romm_token": TOKEN}]


def test_a_romm_code_the_server_refuses_saves_nothing(monkeypatch):
    monkeypatch.setattr(settings.romm_handler, "exchange", lambda address, code: None)
    for row, status in ((Settings(romm_url="https://romm.example"), 400), (Settings(), 422)):
        try:
            _put(monkeypatch, row, romm_token="12345678")
        except settings.HTTPException as e:
            assert e.status_code == status
        else:
            raise AssertionError("the code was accepted")


def test_a_romm_value_that_is_neither_token_nor_code_is_refused(monkeypatch):
    try:
        _put(monkeypatch, Settings(romm_url="https://romm.example"), romm_token="hunter2")
    except settings.HTTPException as e:
        assert e.status_code == 422
    else:
        raise AssertionError("accepted")


def test_sending_back_the_masked_romm_token_keeps_it(monkeypatch):
    row = Settings(romm_url="https://romm.example", romm_token=TOKEN)
    assert _put(monkeypatch, row, romm_token=settings.mask_secret(TOKEN)) == [{}]
