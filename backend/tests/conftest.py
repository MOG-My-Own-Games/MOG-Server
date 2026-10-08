import pytest


@pytest.fixture(autouse=True)
def _no_provider_calls_from_scrapes(monkeypatch, request):
    """Scrape tests mock the providers they care about; the artwork step must never reach out for real."""
    import config
    from handler import scrape_handler

    # Tests of HowLongToBeat itself switch it back on and mock the HTTP.
    monkeypatch.setattr(config, "HLTB_ENABLED", False)
    if request.node.get_closest_marker("real_media"):
        return
    monkeypatch.setattr(scrape_handler, "_store_media", lambda *args, **kwargs: False)


@pytest.fixture(autouse=True)
def _default_settings_row(monkeypatch, request):
    """Handlers read the provider switches from the settings row; without the `db` fixture there is no table, so
    they read an untouched row (every switch at its default)."""
    if "db" in request.fixturenames:
        return
    from handler.database import db_settings_handler
    from models.settings import Settings

    monkeypatch.setattr(db_settings_handler, "get_settings", lambda: Settings())


@pytest.fixture
def db(monkeypatch, tmp_path):
    """The real handlers on a throwaway SQLite file with the whole schema created."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import decorators.database
    from models import load_all_models
    from models.base import BaseModel

    load_all_models()
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    BaseModel.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(decorators.database, "sync_session", session_factory)
    yield engine
    engine.dispose()
