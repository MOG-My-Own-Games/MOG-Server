import io
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from endpoints import games as games_endpoints
from handler import media as media_handler
from handler import media_uploads, scrape_handler
from handler.auth import get_current_user
from handler.database import db_game_handler, db_library_handler, db_user_handler
from models.game import Game
from models.library import Library
from models.user import Role, User
from utils import image_cache


def _image(fmt="PNG", size=(40, 30), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    monkeypatch.setattr(media_uploads, "RESOURCES_BASE_PATH", str(tmp_path / "resources"))
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "resources" / "imagecache")
    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    plain = db_user_handler.add_user(User(username="kid", hashed_password="x", role=Role.USER))
    who = {"user": admin}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    game = db_game_handler.add_game(Game(library_id=lib.id, fs_name="g", name="G"))
    return SimpleNamespace(client=TestClient(app), game=game, who=who, plain=plain, root=tmp_path / "resources" / "media")


def _upload(world, kind="cover", data=None, name="art.png"):
    return world.client.post(
        f"/api/games/{world.game.id}/media/{kind}", files={"file": (name, _image() if data is None else data, "image/png")}
    )


def test_an_uploaded_cover_becomes_the_games_cover_and_is_served_shrunk(world):
    response = _upload(world, "cover", _image(size=(900, 1600)))

    assert response.status_code == 200
    body = response.json()
    url = body["media"]["cover"]["url"]
    assert body["media"]["cover"]["source"] == "upload" and body["cover_path"] == url
    assert url.startswith(f"/resources/media/{world.game.id}/cover-")
    assert (world.root / str(world.game.id) / url.rsplit("/", 1)[1]).is_file()

    served = world.client.get(f"/api/games/{world.game.id}/cover")
    assert served.status_code == 200
    assert Image.open(io.BytesIO(served.content)).height == image_cache.COVER_MAX_HEIGHT  # shrunk, as a provider's is


def test_the_other_kinds_are_served_as_uploaded(world):
    _upload(world, "hero", _image(size=(1920, 620)))

    served = world.client.get(f"/api/games/{world.game.id}/media/hero")

    assert served.status_code == 200 and Image.open(io.BytesIO(served.content)).size == (1920, 620)


@pytest.mark.parametrize("fmt", ["WEBP", "GIF"])
def test_webp_and_gif_are_kept_as_png_so_every_client_can_show_them(world, fmt):
    assert _upload(world, "logo", _image(fmt)).json()["media"]["logo"]["url"].endswith(".png")


def test_a_new_upload_of_a_kind_replaces_the_old_file(world):
    first = _upload(world, "banner", _image(color=(1, 2, 3))).json()["media"]["banner"]["url"]
    second = _upload(world, "banner", _image(color=(9, 9, 9))).json()["media"]["banner"]["url"]

    files = sorted(p.name for p in (world.root / str(world.game.id)).iterdir())
    assert first != second and files == [second.rsplit("/", 1)[1]]


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"", "empty"),
        (b"not an image at all", "PNG, JPEG"),
        (b"\x89PNG\r\n\x1a\n" + b"truncated", "usable image"),
        (_image()[:40], "usable image"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "PNG, JPEG"),
        (b"x" * (media_uploads.MAX_UPLOAD_BYTES + 1), "larger"),
    ],
)
def test_what_is_not_an_artwork_is_refused_and_kept_nowhere(world, data, why):
    response = _upload(world, "cover", data)

    assert response.status_code == 400 and why in response.json()["detail"]
    assert not world.root.exists() or not any(world.root.rglob("*.*"))
    assert db_game_handler.get_game(world.game.id).media in (None, {})


def test_only_the_artwork_kinds_are_taken_and_only_for_a_game_that_exists(world):
    assert _upload(world, "screenshot").status_code == 404
    assert world.client.post("/api/games/999/media/cover", files={"file": ("a.png", _image(), "image/png")}).status_code == 404


def test_a_user_who_is_not_an_admin_cannot_upload(world):
    world.who["user"] = world.plain

    assert _upload(world).status_code == 403


def test_the_upload_is_on_offer_among_the_candidates_and_choosing_none_removes_the_file(world, monkeypatch):
    monkeypatch.setattr(media_handler.sgdb_handler, "get_media", lambda *a, **k: {})
    url = _upload(world, "icon").json()["media"]["icon"]["url"]

    offered = world.client.get(f"/api/games/{world.game.id}/media/candidates").json()["candidates"]["icon"]
    assert offered[0]["url"] == url and offered[0]["source"] == "upload"

    cleared = world.client.put(f"/api/games/{world.game.id}/media", json={"icon": None})
    assert cleared.status_code == 200 and "icon" not in cleared.json()["media"]
    assert not (world.root / str(world.game.id) / url.rsplit("/", 1)[1]).exists()


def test_choosing_a_providers_image_over_an_upload_removes_the_upload_and_the_same_upload_can_be_sent_again(world):
    url = _upload(world, "hero").json()["media"]["hero"]["url"]
    again = world.client.put(f"/api/games/{world.game.id}/media", json={"hero": url})
    assert again.status_code == 200 and again.json()["media"]["hero"]["url"] == url

    other = world.client.put(f"/api/games/{world.game.id}/media", json={"hero": "https://cdn2.steamgriddb.com/hero/a.png"})

    assert other.status_code == 200 and other.json()["media"]["hero"]["source"] == "steamgriddb"
    assert not (world.root / str(world.game.id) / url.rsplit("/", 1)[1]).exists()


def test_an_arbitrary_local_path_is_not_accepted_as_a_choice(world):
    for bad in ("/resources/media/1/../../secret.png", "/etc/passwd", "/resources/media/1/cover-aaaaaaaaaaaa.png"):
        response = world.client.put(f"/api/games/{world.game.id}/media", json={"cover": bad})
        assert response.status_code == 400


@pytest.mark.real_media
def test_a_scrape_that_replaces_the_artwork_leaves_the_uploaded_kinds_alone(world, monkeypatch):
    url = _upload(world, "cover").json()["media"]["cover"]["url"]
    monkeypatch.setattr(
        media_handler,
        "candidates",
        lambda game, sgdb_id=None: {
            "cover": [{"url": "https://cdn2.steamgriddb.com/c.png", "source": "steamgriddb"}],
            "hero": [{"url": "https://cdn2.steamgriddb.com/h.png", "source": "steamgriddb"}],
        },
    )

    scrape_handler._store_media(db_game_handler.get_game(world.game.id), None, replace=True)

    game = db_game_handler.get_game(world.game.id)
    assert game.media["cover"] == {"url": url, "source": "upload"} and game.cover_path == url
    assert game.media["hero"]["url"] == "https://cdn2.steamgriddb.com/h.png"  # the kinds without an upload are scraped as before


def test_the_path_of_an_upload_never_leaves_the_media_folder(world):
    assert media_uploads.path_for("/resources/media/1/../../x.png") is None
    assert media_uploads.path_for("/resources/media/1/cover-0123456789ab.png") == world.root / "1" / "cover-0123456789ab.png"
    assert media_uploads.path_for("https://example.com/resources/media/1/cover-0123456789ab.png") is None
    assert media_uploads.path_for(None) is None


def test_uploads_go_with_the_game(world):
    _upload(world, "cover")
    assert (world.root / str(world.game.id)).is_dir()

    media_uploads.purge(world.game.id)

    assert not (world.root / str(world.game.id)).exists()
