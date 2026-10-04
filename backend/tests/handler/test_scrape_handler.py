from unittest.mock import patch

from handler.scrape_handler import search_names, scrape_game, search_name
from models.game import Game


def _game(**overrides) -> Game:
    defaults = dict(id=1, library_id=1, fs_name="Some Game", name="Some Game", igdb_id=None, igdb_metadata=None, sgdb_id=None, cover_path=None)
    return Game(**{**defaults, **overrides})


class TestScrapeGame:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_applies_top_igdb_result_and_hands_the_artwork_to_the_media_step(self, igdb, sgdb, db):
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
        sgdb.search_games.return_value = [{"id": 7, "name": "Some Game"}]

        with patch("handler.scrape_handler._store_media", return_value=True) as store:
            applied = scrape_game(_game())

        assert applied is True
        assert store.call_args.args[1] == 7 and store.call_args.kwargs == {"replace": False}
        igdb.get_game_by_id.assert_called_once_with(42)
        igdb_call = db.update_game.call_args_list[0]
        assert igdb_call.args[1]["igdb_id"] == 42
        assert igdb_call.args[1]["igdb_metadata"]["genres"] == [{"name": "Adventure"}]
        assert db.update_game.call_args_list[1].args[1] == {"sgdb_id": 7}

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_already_matched_game_is_not_re_searched(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_games.return_value = []
        sgdb.get_grids.return_value = []

        applied = scrape_game(
            _game(igdb_id=99, igdb_metadata={"id": 99}, cover_path="x", media={"cover": {"url": "x", "source": "steamgriddb"}})
        )

        assert applied is False
        igdb.search_games.assert_not_called()
        sgdb.search_games.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_no_results_applies_nothing(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_games.return_value = []
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

        with patch("handler.scrape_handler._store_media", return_value=True) as store:
            applied = scrape_game(_game(igdb_id=99, sgdb_id=5))

        assert applied is True
        igdb.search_games.assert_not_called()
        sgdb.search_games.assert_not_called()
        igdb.get_game_by_id.assert_called_once_with(99)
        assert store.call_args.args[1] == 5


class TestSearchName:
    def test_cleans_release_noise(self):
        assert search_name("Half-Life 2 (GOG) [Multi]") == "Half-Life 2"
        assert search_name("The_Witcher_3_v1.31.exe") == "The Witcher 3"
        assert search_name("Portal 2 - GOG") == "Portal 2"

    def test_scene_name_dots_and_group(self):
        assert search_name("FANTASY.LIFE.i.The.Girl.Who.Steals.Time-TENOKE") == "FANTASY LIFE i The Girl Who Steals Time"

    def test_scene_group_with_mixed_case(self):
        assert search_names("Broken.Reality-TiNYiSO") == ["Broken Reality", "Broken Reality TiNYiSO"]
        assert search_name("Dark.Souls.III-CODEX") == "Dark Souls III"
        assert search_name("Game.Name-razor1911") == "Game Name"

    def test_release_tags_and_versions_are_dropped(self):
        assert search_name("Some.Game.MULTi12.REPACK-GROUP") == "Some Game"
        assert search_name("Game.Name.v1.2.3-RUNE") == "Game Name"
        assert search_name("Game.Name.Build.12345-SKIDROW") == "Game Name"
        assert search_name("Some Game - FLT") == "Some Game"

    def test_the_stripped_group_is_kept_as_a_fallback_title(self):
        assert search_names("Some Game - FLT") == ["Some Game", "Some Game FLT"]

    def test_hyphenated_title_survives(self):
        assert search_name("Half-Life") == "Half-Life"
        assert search_name("Marvels.Spider-Man") == "Marvels Spider-Man"

    def test_plain_name_untouched(self):
        assert search_name("Some Game") == "Some Game"


class TestRefreshGame:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_refetches_igdb_and_cover_by_the_new_ids(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.get_game_by_id.return_value = {"id": 9, "name": "New", "summary": "S", "genres": [{"name": "RPG"}]}

        with patch("handler.scrape_handler._store_media", return_value=True) as store:
            assert refresh_game(_game(igdb_id=9, sgdb_id=5, cover_path="old.png"))

        igdb.search_games.assert_not_called()
        sgdb.search_games.assert_not_called()
        updates = [c.args[1] for c in db.update_game.call_args_list]
        assert {"igdb_id": 9, "igdb_metadata": igdb.get_game_by_id.return_value, "summary": "S", "name": "New"} in updates
        assert store.call_args.args[1] == 5 and store.call_args.kwargs["replace"] is True

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_keeps_fields_edited_by_hand_and_searches_by_name(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.search_games.return_value = [{"id": 3, "name": "My Name"}]
        igdb.get_game_by_id.return_value = {"id": 3, "name": "My Name", "summary": "S"}
        sgdb.search_games.return_value = [{"id": 8, "name": "My Name"}]
        sgdb.get_grids.return_value = ["https://example.com/c.png"]

        refresh_game(_game(name="My Name"), keep=frozenset({"name", "cover_path"}))

        updates = [c.args[1] for c in db.update_game.call_args_list]
        assert all("name" not in u and "cover_path" not in u for u in updates)
        igdb.search_games.assert_called_with("My Name")


class TestFuzzyMatching:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_picks_best_scoring_result_not_the_first(self, igdb, sgdb, db):
        igdb.search_games.return_value = [
            {"id": 1, "name": "Fantasy Life"},
            {"id": 2, "name": "Fantasy Life i: The Girl Who Steals Time"},
        ]
        igdb.get_game_by_id.return_value = {"id": 2, "name": "Fantasy Life i: The Girl Who Steals Time"}
        sgdb.search_games.return_value = []

        scrape_game(_game(name="Fantasy.Life.i.The.Girl.Who.Steals.Time-TENOKE", fs_name="x"))

        igdb.get_game_by_id.assert_called_once_with(2)

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_trailing_noise_is_dropped_by_retrying_shorter_queries(self, igdb, sgdb, db):
        igdb.search_games.side_effect = lambda q: [] if "zzz" in q else [{"id": 5, "name": "Some Game"}]
        igdb.get_game_by_id.return_value = {"id": 5, "name": "Some Game"}
        sgdb.search_games.return_value = []

        scrape_game(_game(name="Some Game zzz", fs_name="Some Game zzz"))

        igdb.get_game_by_id.assert_called_once_with(5)

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_unrelated_results_are_rejected(self, igdb, sgdb, db):
        igdb.search_games.return_value = [{"id": 9, "name": "Completely Different Title"}]
        sgdb.search_games.return_value = []

        assert scrape_game(_game(name="Some Game", fs_name="Some Game")) is False
        igdb.get_game_by_id.assert_not_called()


class TestScrapeLibrary:
    @patch("handler.scrape_handler.scrape_game")
    @patch("handler.scrape_handler.db_game_handler")
    def test_skips_missing_games_and_survives_a_failure(self, db, scrape, caplog):
        from handler.scrape_handler import scrape_library

        db.get_games_for_library.return_value = [
            _game(id=1, name="boom"),
            _game(id=2, name="gone", missing_from_fs=True),
            _game(id=3, name="ok"),
        ]
        scrape.side_effect = lambda g: (_ for _ in ()).throw(RuntimeError("x")) if g.id == 1 else True

        result = scrape_library(1)

        assert [c.args[0].id for c in scrape.call_args_list] == [1, 3]
        assert (result.total, result.scraped) == (2, 1)

    def test_needs_scrape_only_for_present_games_missing_a_match_or_cover(self):
        from handler.scrape_handler import needs_scrape

        picked = {"cover": {"url": "c.png", "source": "steamgriddb"}}
        assert needs_scrape(_game())
        assert needs_scrape(_game(igdb_id=1))
        assert needs_scrape(_game(igdb_id=1, cover_path="c.png"))  # covered, but no artwork chosen yet
        assert not needs_scrape(_game(igdb_id=1, cover_path="c.png", media=picked))
        assert not needs_scrape(_game(missing_from_fs=True))


class TestExplicitScrapeReplaces:
    @patch("handler.scrape_handler.refresh_game")
    @patch("handler.scrape_handler.scrape_game")
    @patch("handler.scrape_handler.db_game_handler")
    def test_library_refresh_replaces_while_the_default_only_fills(self, db, fill, refresh):
        from handler.scrape_handler import scrape_library

        db.get_games_for_library.return_value = [_game(id=1), _game(id=2, missing_from_fs=True)]
        fill.return_value = refresh.return_value = True

        scrape_library(1)
        assert fill.call_count == 1 and refresh.call_count == 0

        scrape_library(1, refresh=True)
        assert fill.call_count == 1 and refresh.call_count == 1


class TestRematchCover:
    def _run(self, igdb, sgdb, db, rematch):
        from handler.scrape_handler import refresh_game

        igdb.get_game_by_id.return_value = {"id": 9, "name": "Right Game"}
        sgdb.search_games.return_value = [{"id": 8, "name": "Right Game"}]
        with patch("handler.scrape_handler._store_media", return_value=True) as store:
            refresh_game(_game(name="Right Game", igdb_id=9, sgdb_id=5), rematch_cover=rematch)
        return store.call_args.args[1], [c.args[1] for c in db.update_game.call_args_list]

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_rematch_replaces_a_cover_matched_to_the_wrong_game(self, igdb, sgdb, db):
        used, updates = self._run(igdb, sgdb, db, rematch=True)
        assert used == 8 and {"sgdb_id": 8} in updates

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_without_rematch_the_stored_sgdb_id_is_trusted(self, igdb, sgdb, db):
        used, _ = self._run(igdb, sgdb, db, rematch=False)
        assert used == 5
        sgdb.search_games.assert_not_called()


class TestNameFromTheMatch:
    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_igdb_match_renames_the_game(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.get_game_by_id.return_value = {"id": 9, "name": "The Real Title"}
        sgdb.search_games.return_value = []
        refresh_game(_game(name="the.real.title-GRP", fs_name="the.real.title-GRP", igdb_id=9))
        assert any(c.args[1].get("name") == "The Real Title" for c in db.update_game.call_args_list)

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_without_igdb_a_raw_name_takes_the_steamgriddb_one(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.search_games.return_value = []
        sgdb.search_games.return_value = [{"id": 4, "name": "Some Game"}]
        refresh_game(_game(name="some.game", fs_name="some.game"))
        updates = [c.args[1] for c in db.update_game.call_args_list]
        assert {"sgdb_id": 4, "name": "Some Game"} in updates

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_name_set_by_hand_is_not_overwritten_by_steamgriddb(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.search_games.return_value = []
        sgdb.search_games.return_value = [{"id": 4, "name": "Provider Name"}]
        refresh_game(_game(name="My Name", fs_name="raw-folder"), rematch_cover=True)
        assert all("name" not in c.args[1] for c in db.update_game.call_args_list)
