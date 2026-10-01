from unittest.mock import patch

from handler.scrape_handler import scrape_game
from models.game import Game


def _game(**overrides) -> Game:
    defaults = dict(id=1, library_id=1, fs_name="Some Game", name="Some Game", igdb_id=None, cover_path=None)
    return Game(**{**defaults, **overrides})


class TestScrapeGame:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_applies_top_igdb_result_and_top_cover(self, igdb, sgdb, db):
        igdb.search_games.return_value = [{"id": 42, "name": "Some Game", "summary": "A game."}]
        sgdb.search_grids.return_value = ["https://example.com/cover.png"]

        applied = scrape_game(_game())

        assert applied is True
        igdb_call = db.update_game.call_args_list[0]
        assert igdb_call.args[1]["igdb_id"] == 42
        assert igdb_call.args[1]["igdb_metadata"]["id"] == 42
        cover_call = db.update_game.call_args_list[1]
        assert cover_call.args[1]["cover_path"] == "https://example.com/cover.png"

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_already_matched_game_is_not_re_searched(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_grids.return_value = []

        applied = scrape_game(_game(igdb_id=99, cover_path="already-set.png"))

        assert applied is False
        igdb.search_games.assert_not_called()
        sgdb.search_grids.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_no_results_applies_nothing(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_grids.return_value = []

        applied = scrape_game(_game())

        assert applied is False
        db.update_game.assert_not_called()
