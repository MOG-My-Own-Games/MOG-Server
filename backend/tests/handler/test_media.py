import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from endpoints import games
from endpoints.responses.game import MediaSelectionForm
from fastapi import HTTPException
from handler import media
from handler.metadata import sgdb_handler
from models.game import Game
from utils import image_cache


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

    def test_nothing_on_offer_changes_nothing(self):
        ok, saved = self._store(_game(), {}, replace=True)
        assert ok is False and saved == {}


class TestMediaEndpoints:
    def _put(self, monkeypatch, game, **body):
        saved = {}
        monkeypatch.setattr(games.db_game_handler, "get_game", lambda _id: game)
        monkeypatch.setattr(games.db_game_handler, "update_game", lambda gid, data: saved.update(data) or SimpleNamespace(**{**game.__dict__, **data}))
        monkeypatch.setattr(games.GameSchema, "model_validate", staticmethod(lambda obj: obj))
        result = asyncio.run(games.choose_media(SimpleNamespace(), 1, MediaSelectionForm(**body)))
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
    monkeypatch.setattr(image_cache.httpx, "get", lambda url, **kw: SimpleNamespace(content=out.getvalue(), raise_for_status=lambda: None))

    path = image_cache.cached_image("https://cdn2.steamgriddb.com/logo.png")

    assert path.suffix == ".png" and image_cache.media_type(path) == "image/png"
    assert Image.open(path).mode == "RGBA"
    assert image_cache.cached_image("https://cdn2.steamgriddb.com/logo.png") == path  # second time: from the cache
