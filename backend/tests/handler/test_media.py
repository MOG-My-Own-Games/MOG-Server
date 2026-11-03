import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from endpoints import games
from endpoints.responses.game import MediaSelectionForm
from fastapi import HTTPException
from handler import cover_art, media, media_uploads
from handler.metadata import sgdb_handler
from models.game import Game
from utils import image_cache


def _png(size: tuple[int, int]) -> bytes:
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (90, 40, 140)).save(out, "PNG")
    return out.getvalue()


def _game(**kw) -> Game:
    base = dict(id=1, library_id=1, fs_name="G", name="G", igdb_id=None, igdb_metadata=None, sgdb_id=None, cover_path=None, media=None)
    return Game(**{**base, **kw})


class TestSources:
    def test_only_the_providers_hosts_are_known(self):
        assert media.source_of("https://cdn2.steamgriddb.com/grid/a.png") == media.SGDB
        assert media.source_of("https://images.igdb.com/igdb/image/upload/t_1080p/x.jpg") == media.IGDB
        for bad in ("https://evil.example/a.png", "https://steamgriddb.com.evil.example/a.png", "file:///etc/passwd", "ftp://cdn2.steamgriddb.com/a"):
            assert media.source_of(bad) is None


class TestCandidates:
    def test_steamgriddb_comes_first_and_igdb_fills_cover_and_hero(self):
        game = _game(
            sgdb_id=5,
            igdb_metadata={
                "cover": {"url": "https://images.igdb.com/c.jpg"},
                "artworks": [{"url": "https://images.igdb.com/art.jpg"}],
                "screenshots": [{"url": "https://images.igdb.com/shot.jpg"}],
            },
        )
        sgdb = {"cover": [{"url": "https://cdn2.steamgriddb.com/p.png", "thumb": "https://cdn2.steamgriddb.com/t.png", "width": 600, "height": 900}], "logo": [{"url": "https://cdn2.steamgriddb.com/l.png"}]}
        with patch.object(media.sgdb_handler, "get_media", return_value=sgdb):
            found = media.candidates(game)

        assert [c["source"] for c in found["cover"]] == [media.SGDB, media.IGDB]
        assert [c["url"] for c in found["hero"]] == ["https://images.igdb.com/art.jpg", "https://images.igdb.com/shot.jpg"]
        assert found["logo"][0]["url"].endswith("l.png") and found["banner"] == [] and found["icon"] == []

    def test_defaults_are_the_first_of_each_kind(self):
        found = {"cover": [{"url": "a", "source": "steamgriddb"}, {"url": "b", "source": "igdb"}], "banner": [], "logo": [{"url": "l", "source": "steamgriddb"}]}
        assert media.defaults(found) == {"cover": {"url": "a", "source": "steamgriddb"}, "logo": {"url": "l", "source": "steamgriddb"}}

    def test_a_duplicate_image_counts_once(self):
        game = _game(sgdb_id=5, igdb_metadata={"cover": {"url": "https://cdn2.steamgriddb.com/p.png"}})
        with patch.object(media.sgdb_handler, "get_media", return_value={"cover": [{"url": "https://cdn2.steamgriddb.com/p.png"}]}):
            assert len(media.candidates(game)["cover"]) == 1


class TestSteamGridDbMedia:
    def test_each_kind_is_asked_from_its_own_endpoint(self, monkeypatch):
        asked = []
        monkeypatch.setattr(sgdb_handler, "_api_key", lambda: "k")
        monkeypatch.setattr(sgdb_handler, "_get", lambda path, key: asked.append(path) or [{"url": f"https://cdn2.steamgriddb.com/{len(asked)}.png", "width": 1, "height": 2}])
        found = sgdb_handler.get_media(9)
        assert set(found) == {"cover", "banner", "hero", "logo", "icon"}
        assert any("grids/game/9?dimensions=600x900" in p for p in asked) and any("grids/game/9?dimensions=920x430" in p for p in asked)
        assert any(p.startswith("heroes/game/9") for p in asked) and any(p.startswith("logos/game/9") for p in asked) and any(p.startswith("icons/game/9") for p in asked)
        assert found["cover"][0]["thumb"] == found["cover"][0]["url"]  # no thumb given: the image itself

    def test_no_key_no_calls(self, monkeypatch):
        monkeypatch.setattr(sgdb_handler, "_api_key", lambda: None)
        assert sgdb_handler.get_media(9) == {}


@pytest.mark.real_media
class TestStoreMedia:
    def _store(self, game, chosen, **kw):
        from handler import scrape_handler

        saved = {}
        with patch.object(scrape_handler, "db_game_handler") as db, patch.object(scrape_handler.media_handler, "candidates", return_value={}), patch.object(scrape_handler.media_handler, "defaults", return_value=chosen):
            db.get_game.return_value = game
            db.update_game.side_effect = lambda gid, data: saved.update(data)
            result = scrape_handler._store_media(game, 5, **kw)
        return result, saved

    def test_a_scrape_replaces_every_kind_and_the_cover_path_follows(self):
        game = _game(cover_path="old", media={"cover": {"url": "old", "source": "igdb"}, "logo": {"url": "mine", "source": "steamgriddb"}})
        chosen = {"cover": {"url": "new", "source": "steamgriddb"}, "hero": {"url": "h", "source": "steamgriddb"}}
        ok, saved = self._store(game, chosen, replace=True)
        assert ok and saved["media"] == {"cover": chosen["cover"], "logo": game.media["logo"], "hero": chosen["hero"]}
        assert saved["cover_path"] == "new"

    def test_the_automatic_pass_only_fills_what_is_missing(self):
        game = _game(cover_path="mine", media={"cover": {"url": "mine", "source": "steamgriddb"}})
        chosen = {"cover": {"url": "other", "source": "steamgriddb"}, "logo": {"url": "l", "source": "steamgriddb"}}
        ok, saved = self._store(game, chosen, replace=False)
        assert saved["media"]["cover"]["url"] == "mine" and saved["media"]["logo"]["url"] == "l"
        assert "cover_path" not in saved

    def test_a_kept_cover_is_not_replaced(self):
        game = _game(cover_path="typed", media={"cover": {"url": "typed", "source": "steamgriddb"}})
        ok, saved = self._store(game, {"cover": {"url": "new", "source": "steamgriddb"}}, replace=True, keep={"cover"})
        assert saved.get("media", game.media)["cover"]["url"] == "typed" and "cover_path" not in saved

    def test_a_kind_a_person_removed_stays_empty_for_the_automatic_pass_and_comes_back_with_the_scrape_they_ask_for(self):
        game = _game(cover_path=None, media={"logo": {"url": "l", "source": "steamgriddb"}}, media_cleared=["cover"])
        chosen = {"cover": {"url": "c", "source": "steamgriddb"}, "hero": {"url": "h", "source": "steamgriddb"}}

        ok, saved = self._store(game, chosen, replace=False)
        assert "cover" not in saved["media"] and saved["media"]["hero"]["url"] == "h" and "cover_path" not in saved

        ok, saved = self._store(game, chosen, replace=True)
        assert saved["media"]["cover"]["url"] == "c" and saved["cover_path"] == "c" and saved["media_cleared"] == []

    def test_a_game_with_a_cover_and_nothing_on_offer_is_left_alone(self):
        game = _game(cover_path="c", media={"cover": {"url": "c", "source": "steamgriddb"}})
        ok, saved = self._store(game, {}, replace=True)
        assert ok is False and saved == {}

    def test_a_game_with_no_art_at_all_is_given_a_poster_with_its_name(self):
        ok, saved = self._store(_game(name="Ryona no Mori"), {}, replace=True)
        cover = saved["media"]["cover"]
        assert ok is False  # no provider gave anything: it is a poster, not a scrape that found something
        assert cover["source"] == "generated" and cover["from"] is None and saved["cover_path"] == cover["url"]
        assert media_uploads.is_upload(cover["url"]) and media_uploads.path_for(cover["url"]).is_file()

    def test_a_cover_that_is_not_as_tall_as_the_cards_is_set_whole_on_a_tall_canvas(self, monkeypatch):
        from handler import scrape_handler

        monkeypatch.setattr(scrape_handler, "_image_bytes", lambda entry: _png((460, 215)))
        game = _game(name="Lustful Apocalypse")
        chosen = {"cover": {"url": "https://shared.steamstatic.com/h.jpg", "source": "steam"}}
        ok, saved = self._store(game, chosen, replace=False)
        cover = saved["media"]["cover"]
        assert cover["source"] == "generated" and cover["from"] == chosen["cover"]["url"] and saved["cover_path"] == cover["url"]
        assert cover_art.size_of(media_uploads.path_for(cover["url"]).read_bytes()) == (600, 900)

    def test_a_cover_that_is_tall_enough_is_kept_as_it_is(self, monkeypatch):
        from handler import scrape_handler

        monkeypatch.setattr(scrape_handler, "_image_bytes", lambda entry: _png((600, 900)))
        chosen = {"cover": {"url": "https://cdn2.steamgriddb.com/c.png", "source": "steamgriddb"}}
        ok, saved = self._store(_game(), chosen, replace=False)
        assert saved["media"]["cover"] == chosen["cover"]

    def test_a_game_with_only_a_banner_gets_a_cover_made_of_it(self, monkeypatch):
        from handler import scrape_handler

        monkeypatch.setattr(scrape_handler, "_image_bytes", lambda entry: _png((920, 430)))
        game = _game(cover_path=None, media={"banner": {"url": "https://cdn2.steamgriddb.com/b.png", "source": "steamgriddb"}})
        ok, saved = self._store(game, {}, replace=False)
        assert saved["media"]["cover"]["from"] == "https://cdn2.steamgriddb.com/b.png"

    def test_a_cover_the_person_removed_is_not_made_again_by_the_automatic_pass(self):
        ok, saved = self._store(_game(media_cleared=["cover"]), {}, replace=False)
        assert ok is False and saved == {}

    def test_a_made_cover_gives_way_to_a_real_one_and_its_file_goes(self, monkeypatch):
        from handler import scrape_handler

        made = scrape_handler._tall_cover(_game(name="X"), {})
        old_path = media_uploads.path_for(made["url"])
        game = _game(cover_path=made["url"], media={"cover": made})
        monkeypatch.setattr(scrape_handler, "_image_bytes", lambda entry: _png((600, 900)))
        chosen = {"cover": {"url": "https://cdn2.steamgriddb.com/real.png", "source": "steamgriddb"}}

        ok, saved = self._store(game, chosen, replace=False)

        assert saved["media"]["cover"] == chosen["cover"] and saved["cover_path"] == chosen["cover"]["url"]
        assert not old_path.exists()

    def test_the_cover_picked_by_default_is_the_first_one_as_tall_as_the_cards(self):
        found = {"cover": [
            {"url": "wide", "source": "steamgriddb", "width": 920, "height": 430},
            {"url": "tall", "source": "steamgriddb", "width": 600, "height": 900},
        ]}
        assert media.defaults(found)["cover"]["url"] == "tall"
        assert media.defaults({"cover": [{"url": "x", "source": "igdb", "width": None, "height": None}]})["cover"]["url"] == "x"


class TestMediaEndpoints:
    def _put(self, monkeypatch, game, **body):
        saved = {}
        monkeypatch.setattr(games.db_game_handler, "get_game", lambda _id: game)
        monkeypatch.setattr(games.db_game_handler, "update_game", lambda gid, data: saved.update(data) or SimpleNamespace(**{**game.__dict__, **data}))
        monkeypatch.setattr(games.GameSchema, "model_validate", staticmethod(lambda obj: obj))
        monkeypatch.setattr(games.titles, "versions", lambda g, user: [g])  # no other version of the title
        result = asyncio.run(games.choose_media(SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[]), 1, MediaSelectionForm(**body)))
        return result, saved

    def test_choosing_stores_the_url_with_its_source_and_moves_the_cover_path(self, monkeypatch):
        _, saved = self._put(monkeypatch, _game(), cover="https://cdn2.steamgriddb.com/p.png", logo="https://cdn2.steamgriddb.com/l.png")
        assert saved["media"]["logo"] == {"url": "https://cdn2.steamgriddb.com/l.png", "source": "steamgriddb"}
        assert saved["cover_path"] == "https://cdn2.steamgriddb.com/p.png"

    def test_an_image_from_anywhere_else_is_refused(self, monkeypatch):
        with pytest.raises(HTTPException) as err:
            self._put(monkeypatch, _game(), banner="https://evil.example/x.png")
        assert err.value.status_code == 400

    def test_null_clears_a_kind_and_an_unmentioned_kind_stays(self, monkeypatch):
        game = _game(media={"hero": {"url": "https://cdn2.steamgriddb.com/h.png", "source": "steamgriddb"}, "logo": {"url": "https://cdn2.steamgriddb.com/l.png", "source": "steamgriddb"}})
        _, saved = self._put(monkeypatch, game, hero=None)
        assert list(saved["media"]) == ["logo"]

    def test_the_cover_falls_back_to_cover_path_then_igdb(self):
        assert games._chosen_url(_game(cover_path="cp"), "cover") == "cp"
        assert games._chosen_url(_game(igdb_metadata={"cover": {"url": "ig"}}), "cover") == "ig"
        assert games._chosen_url(_game(), "logo") is None


def test_a_png_keeps_its_transparency_and_type(monkeypatch, tmp_path):
    import io

    from PIL import Image

    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    out = io.BytesIO()
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(out, "PNG")
    monkeypatch.setattr(image_cache.httpx, "get", lambda url, **kw: SimpleNamespace(content=out.getvalue(), is_redirect=False, raise_for_status=lambda: None))

    path = image_cache.cached_image("https://cdn2.steamgriddb.com/logo.png")

    assert path.suffix == ".png" and image_cache.media_type(path) == "image/png"
    assert Image.open(path).mode == "RGBA"
    assert image_cache.cached_image("https://cdn2.steamgriddb.com/logo.png") == path  # second time: from the cache


def test_an_address_that_is_not_a_providers_is_never_fetched(monkeypatch, tmp_path):
    """The cover address is typed by an admin: the server must not be made to call whatever host it names."""
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(image_cache.httpx, "get", lambda url, **kw: calls.append(url))

    for url in ("http://169.254.169.254/latest/meta-data", "http://localhost:5000/api/users", "https://evil.example/steamgriddb.com/a.png"):
        assert image_cache.cached_image(url) is None
    assert calls == []


def test_a_redirect_to_another_host_is_not_followed(monkeypatch, tmp_path):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    calls = []

    def get(url, **kw):
        calls.append(url)
        target = SimpleNamespace(url="http://127.0.0.1:5000/secret")
        return SimpleNamespace(is_redirect=True, next_request=target)

    monkeypatch.setattr(image_cache.httpx, "get", get)

    assert image_cache.cached_image("https://cdn2.steamgriddb.com/a.png") is None
    assert calls == ["https://cdn2.steamgriddb.com/a.png"]


def test_something_that_is_not_an_image_is_not_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        image_cache.httpx, "get", lambda url, **kw: SimpleNamespace(content=b"<html>", is_redirect=False, raise_for_status=lambda: None)
    )
    assert image_cache.cached_image("https://cdn2.steamgriddb.com/a.png") is None
    assert list(tmp_path.iterdir()) == []
