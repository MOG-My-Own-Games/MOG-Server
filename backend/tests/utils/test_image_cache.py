import io

from PIL import Image
from utils import image_cache


class _Response:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def _png(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, "PNG")
    return out.getvalue()


def test_cover_is_downloaded_once_and_shrunk(monkeypatch, tmp_path):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(image_cache.httpx, "get", lambda url, **kw: calls.append(url) or _Response(_png(1200, 1800)))

    first = image_cache.cached_image("https://cdn.example/c.png", 600)
    second = image_cache.cached_image("https://cdn.example/c.png", 600)

    assert first == second and len(calls) == 1
    assert Image.open(first).height == 600


def test_non_http_urls_and_failures_give_none(monkeypatch, tmp_path):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    assert image_cache.cached_image("file:///etc/passwd") is None

    def boom(url, **kw):
        raise image_cache.httpx.ConnectError("down")

    monkeypatch.setattr(image_cache.httpx, "get", boom)
    assert image_cache.cached_image("https://cdn.example/x.png") is None
