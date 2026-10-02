from unittest.mock import patch

from handler.scrape_handler import scrape_game, search_name
from models.game import Game


def _game(**overrides) -> Game:
    defaults = dict(id=1, library_id=1, fs_name="Some Game", name="Some Game", igdb_id=None, igdb_metadata=None, sgdb_id=None, cover_path=None)
    return Game(**{**defaults, **overrides})


class TestScrapeGame:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_applies_top_igdb_result_and_top_cover(self, igdb, sgdb, db):
        # search_games's own result is deliberately sparse (no genres/etc.)
        # to prove get_game_by_id's richer result is what actually gets
        # stored - see scrape_game's own comment on why that re-fetch exists.
        igdb.search_games.return_value = [{"id": 42, "name": "Some Game"}]
        igdb.get_game_by_id.return_value = {
            "id": 42,
            "name": "Some Game",
            "summary": "A game.",
            "genres": [{"name": "Adventure"}],
        }
        sgdb.search_game_id.return_value = 7
        sgdb.get_grids.return_value = ["https://example.com/cover.png"]

        applied = scrape_game(_game())

        assert applied is True
        igdb.get_game_by_id.assert_called_once_with(42)
        igdb_call = db.update_game.call_args_list[0]
        assert igdb_call.args[1]["igdb_id"] == 42
        assert igdb_call.args[1]["igdb_metadata"]["genres"] == [{"name": "Adventure"}]
        cover_call = db.update_game.call_args_list[1]
        assert cover_call.args[1]["cover_path"] == "https://example.com/cover.png"

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_already_matched_game_is_not_re_searched(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_game_id.return_value = None
        sgdb.get_grids.return_value = []

        applied = scrape_game(_game(igdb_id=99, igdb_metadata={"id": 99}, cover_path="already-set.png"))

        assert applied is False
        igdb.search_games.assert_not_called()
        sgdb.search_game_id.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_no_results_applies_nothing(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_game_id.return_value = None
        sgdb.get_grids.return_value = []

        applied = scrape_game(_game())

        assert applied is False
        db.update_game.assert_not_called()


class TestManualIds:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_manual_ids_are_fetched_directly(self, igdb, sgdb, db):
        igdb.get_game_by_id.return_value = {"id": 99, "name": "Real Name", "summary": "S"}
        sgdb.get_grids.return_value = ["https://example.com/g.png"]

        applied = scrape_game(_game(igdb_id=99, sgdb_id=5))

        assert applied is True
        igdb.search_games.assert_not_called()
        sgdb.search_game_id.assert_not_called()
        igdb.get_game_by_id.assert_called_once_with(99)
        sgdb.get_grids.assert_called_once_with(5)
        assert db.update_game.call_args_list[1].args[1] == {"cover_path": "https://example.com/g.png", "sgdb_id": 5}


class TestSearchName:
    def test_cleans_release_noise(self):
        assert search_name("Half-Life 2 (GOG) [Multi]") == "Half-Life 2"
        assert search_name("The_Witcher_3_v1.31.exe") == "The Witcher 3"
        assert search_name("Portal 2 - GOG") == "Portal 2"

    def test_plain_name_untouched(self):
        assert search_name("Some Game") == "Some Game"
