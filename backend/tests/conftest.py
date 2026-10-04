import pytest


@pytest.fixture(autouse=True)
def _no_provider_calls_from_scrapes(monkeypatch, request):
    """Scrape tests mock the providers they care about; the artwork step must never reach out for real."""
    from handler import scrape_handler

    if request.node.get_closest_marker("real_media"):
        return
    monkeypatch.setattr(scrape_handler, "_store_media", lambda *args, **kwargs: False)
