from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config
from handler import scrape_handler
from handler.metadata import hltb_handler
from handler.metadata.hltb_handler import HLTBUnavailable

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

    def test_a_gog_linux_installer_name_is_cut_down_to_the_title(self):
        assert search_name("lost_ruins_1_0_9a_48364.sh") == "lost ruins"
        assert search_name("stardew_valley_1_5_6_54317.sh") == "stardew valley"
        assert search_name("the_witcher_3_wild_hunt_goty_1_32_52_0_2023_04_14_48093.sh") == "the witcher 3 wild hunt"

    def test_a_gog_windows_installer_name_loses_its_setup_word_and_build(self):
        assert search_name("setup_jazz_jackrabbit_collection_2.0_csv2_patch_2_gus_(77533).exe") == "jazz jackrabbit collection"
        assert search_name("setup_tyrian_2000_2.1.0.4.exe") == "tyrian 2000"

    def test_the_other_installer_extensions_are_dropped(self):
        for ext in (".sh", ".run", ".bin", ".msi", ".AppImage", ".iso"):
            assert search_name(f"cool_game{ext}") == "cool game"

    def test_a_number_that_belongs_to_the_title_stays(self):
        assert search_name("Cyberpunk 2077") == "Cyberpunk 2077"
        assert search_name("jazz_jackrabbit_2") == "jazz jackrabbit 2"
        assert search_name("Doom 3 2004") == "Doom 3 2004"
        assert search_name("half_life_2") == "half life 2"
        assert search_name("Left 4 Dead 2") == "Left 4 Dead 2"

    def test_a_long_run_of_version_parts_goes_even_without_a_build_id(self):
        assert search_name("some_game_1_2_3") == "some game"
        assert search_name("some_game_2_0") == "some game 2 0"  # two parts could be a sequel and a version

    def test_a_build_id_alone_goes(self):
        assert search_name("some_game_48364") == "some game"
        assert search_name("some_game_2_48364") == "some game"

    def test_the_name_is_never_emptied(self):
        assert search_name("48364") == "48364"
        assert search_name("setup.exe") == "setup"

    def test_a_year_in_parentheses_is_kept_as_written(self):
        assert search_name("Doom (1993)") == "Doom (1993)"
        assert search_name("Doom (2016) [GOG]") == "Doom (2016)"
        assert search_name("Half-Life 2 (2004).iso") == "Half-Life 2 (2004)"
        assert search_name("Doom_(1993).exe") == "Doom (1993)"

    def test_the_year_survives_a_version_run_that_is_cut(self):
        assert search_name("lost_ruins_1_0_9a_(2019).sh") == "lost ruins (2019)"
        assert search_name("Lost Ruins 1.0.9a (2019)") == "Lost Ruins (2019)"

    def test_brackets_that_are_not_a_year_in_parentheses_still_go(self):
        assert search_name("Game (1234)") == "Game"  # not a plausible year
        assert search_name("Game (2077)") == "Game"  # nor is this
        assert search_name("Game (77533)") == "Game"  # a build id
        assert search_name("Game (12)") == "Game"
        assert search_name("Game [1998]") == "Game"  # only parentheses mark a year
        assert search_name("Game [v26.06.2021]") == "Game"
        assert search_name("setup_game_name_2.0_(77533).exe") == "game name"

    def test_a_year_already_in_the_title_is_not_added_twice(self):
        assert search_name("Cyberpunk 2077 (2077)") == "Cyberpunk 2077"
        assert search_name("Doom (1993) (1993)") == "Doom (1993)"

    def test_a_year_comes_with_the_fallback_title_too(self):
        assert search_names("Some Game (1999) - FLT") == ["Some Game (1999)", "Some Game FLT (1999)"]

    def test_a_scene_name_with_a_version_run(self):
        assert search_name("Lost.Ruins.1.0.9a.48364-GROUP") == "Lost Ruins"


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

    def test_a_scraped_game_never_looked_up_on_hltb_still_needs_a_scrape(self, monkeypatch):
        from handler.scrape_handler import needs_scrape

        done = dict(igdb_id=1, cover_path="c.png", media={"cover": {"url": "c.png", "source": "steamgriddb"}})
        monkeypatch.setattr(config, "HLTB_ENABLED", True)
        assert needs_scrape(_game(**done))
        assert not needs_scrape(_game(**done, hltb_id=0))  # looked up, nothing found
        assert not needs_scrape(_game(**done, hltb_id=7))
        assert needs_scrape(_game(**{**done, "igdb_id": None}, hltb_id=0))  # the IGDB match is what is missing
        monkeypatch.setattr(config, "HLTB_ENABLED", False)
        assert not needs_scrape(_game(**done))


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



class TestAnnouncingAddedGames:
    def _library(self):
        from models.library import Library

        return Library(id=3, name="Games", root_path="/g")

    def test_the_games_are_announced_once_the_scrape_has_named_them(self, monkeypatch):
        calls = []
        monkeypatch.setattr(scrape_handler, "scrape_library_in_background", lambda lib_id: calls.append(("scrape", lib_id)))
        names = {10: "AI Shoujo", 11: "Hearthlands"}  # what the scrape renamed them to
        monkeypatch.setattr(scrape_handler.db_game_handler, "get_game", lambda gid: SimpleNamespace(name=names[gid]))
        monkeypatch.setattr(scrape_handler, "notify_games_added", lambda lib, added: calls.append(("notify", added)))

        scrape_handler.scrape_and_announce(self._library(), ((10, "[Group] AI Shoujo R15"), (11, "Hearthlands [v26]")))

        assert calls == [("scrape", 3), ("notify", ((10, "AI Shoujo"), (11, "Hearthlands")))]

    def test_a_game_that_is_gone_keeps_its_folder_name(self, monkeypatch):
        sent = []
        monkeypatch.setattr(scrape_handler, "scrape_library_in_background", lambda lib_id: None)
        monkeypatch.setattr(scrape_handler.db_game_handler, "get_game", lambda gid: None)
        monkeypatch.setattr(scrape_handler, "notify_games_added", lambda lib, added: sent.append(added))

        scrape_handler.scrape_and_announce(self._library(), ((10, "Folder"),))

        assert sent == [((10, "Folder"),)]

    def test_nothing_added_sends_nothing(self, monkeypatch):
        sent = []
        monkeypatch.setattr(scrape_handler, "scrape_library_in_background", lambda lib_id: None)
        monkeypatch.setattr(scrape_handler, "notify_games_added", lambda lib, added: sent.append(added))

        scrape_handler.scrape_and_announce(self._library(), ())

        assert sent == []

    def test_a_pass_already_running_is_waited_for(self, monkeypatch):
        order = []
        scrape_handler._scraping.add(3)
        monkeypatch.setattr(scrape_handler.time, "sleep", lambda s: scrape_handler._scraping.discard(3) or order.append("waited"))
        monkeypatch.setattr(scrape_handler, "scrape_library_in_background", lambda lib_id: order.append("scrape"))
        monkeypatch.setattr(scrape_handler.db_game_handler, "get_game", lambda gid: SimpleNamespace(name="X"))
        monkeypatch.setattr(scrape_handler, "notify_games_added", lambda lib, added: order.append("notify"))

        scrape_handler.scrape_and_announce(self._library(), ((1, "x"),))

        assert order == ["waited", "scrape", "notify"]

    def test_a_failed_notification_is_only_logged(self, monkeypatch):
        monkeypatch.setattr(scrape_handler, "scrape_library_in_background", lambda lib_id: None)
        monkeypatch.setattr(scrape_handler.db_game_handler, "get_game", lambda gid: SimpleNamespace(name="X"))

        def boom(lib, added):
            raise RuntimeError("db down")

        monkeypatch.setattr(scrape_handler, "notify_games_added", boom)
        scrape_handler.scrape_and_announce(self._library(), ((1, "x"),))


TIMES = {"main_story": 36000, "main_plus_extra": 54000}


class TestHltb:
    """HowLongToBeat is asked only for a game with an IGDB match."""

    @pytest.fixture(autouse=True)
    def _hltb_on(self, monkeypatch):
        monkeypatch.setattr(config, "HLTB_ENABLED", True)

    @staticmethod
    def _matched(**overrides) -> Game:
        base = dict(igdb_id=99, igdb_metadata={"id": 99}, cover_path="x", media={"cover": {"url": "x", "source": "steamgriddb"}})
        return _game(**{**base, **overrides})

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_scraped_game_gets_its_times(self, igdb, sgdb, db):
        with patch.object(hltb_handler, "search_games", return_value=[{"id": 7, "name": "Some Game", "metadata": TIMES}]) as search:
            assert scrape_game(self._matched()) is True

        assert search.call_args_list[0].args == ("Some Game",)
        db.update_game.assert_called_once_with(1, {"hltb_id": 7, "hltb_metadata": TIMES})

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_game_with_no_igdb_match_never_asks(self, igdb, sgdb, db):
        igdb.search_games.return_value = []
        sgdb.search_games.return_value = []

        with patch.object(hltb_handler, "search_games") as search, patch.object(hltb_handler, "get_game_by_id") as by_id:
            scrape_game(_game())

        search.assert_not_called()
        by_id.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_fresh_igdb_match_is_searched_by_the_name_it_gave(self, igdb, sgdb, db):
        igdb.search_games.return_value = [{"id": 42, "name": "Some Game"}]
        igdb.get_game_by_id.return_value = {"id": 42, "name": "Some Game", "summary": "S"}
        sgdb.search_games.return_value = []

        with patch.object(hltb_handler, "search_games", return_value=[]) as search:
            scrape_game(_game(fs_name="some.game-GRP", name="some.game-GRP"))

        assert search.call_args_list[0].args == ("Some Game",)

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_lookup_that_found_nothing_is_recorded_and_not_repeated(self, igdb, sgdb, db):
        with patch.object(hltb_handler, "search_games", return_value=[]) as search:
            assert scrape_game(self._matched()) is False
            db.update_game.assert_called_once_with(1, {"hltb_id": 0})
            search.reset_mock()
            scrape_game(self._matched(hltb_id=0))

        search.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_hltb_being_unreachable_stores_nothing_and_does_not_record_a_miss(self, igdb, sgdb, db):
        with patch.object(hltb_handler, "search_games", side_effect=HLTBUnavailable("blocked")):
            assert scrape_game(self._matched()) is False

        db.update_game.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_game_that_has_its_times_is_left_alone(self, igdb, sgdb, db):
        with patch.object(hltb_handler, "search_games") as search:
            assert scrape_game(self._matched(hltb_id=7, hltb_metadata=TIMES)) is False

        search.assert_not_called()
        db.update_game.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_set_hltb_id_is_fetched_directly(self, igdb, sgdb, db):
        with patch.object(hltb_handler, "search_games") as search, patch.object(
            hltb_handler, "get_game_by_id", return_value={"id": 7, "name": "X", "metadata": TIMES}
        ) as by_id:
            assert scrape_game(self._matched(hltb_id=7)) is True

        search.assert_not_called()
        by_id.assert_called_once_with(7)

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_switched_off_asks_nothing(self, igdb, sgdb, db, monkeypatch):
        monkeypatch.setattr(config, "HLTB_ENABLED", False)
        with patch.object(hltb_handler, "search_games") as search:
            assert scrape_game(self._matched()) is False

        search.assert_not_called()

    @patch("handler.scrape_handler.db_game_handler")
    @patch("handler.scrape_handler.sgdb_handler")
    @patch("handler.scrape_handler.igdb_handler")
    def test_a_refresh_searches_again_and_keeps_the_old_times_on_a_miss(self, igdb, sgdb, db):
        from handler.scrape_handler import refresh_game

        igdb.get_game_by_id.return_value = {"id": 99, "name": "Some Game"}
        sgdb.search_games.return_value = []
        game = self._matched(hltb_id=7, hltb_metadata=TIMES)

        with patch.object(hltb_handler, "get_game_by_id", return_value=None) as by_id:
            refresh_game(game)

        by_id.assert_called_once_with(7)
        assert not any("hltb_id" in c.args[1] or "hltb_metadata" in c.args[1] for c in db.update_game.call_args_list)


class TestVideosAfterAScrape:
    @patch("handler.scrape_handler.scrape_game", return_value=False)
    @patch("handler.scrape_handler.db_game_handler")
    def test_a_scrape_has_the_videos_of_the_library_looked_up_in_the_background(self, db, _scrape, monkeypatch):
        from handler import video_handler

        videos = [{"name": "Trailer", "video_id": "aaaaaaaaaaa"}]
        db.get_games_for_library.return_value = [
            _game(id=1, name="Alpha", igdb_metadata={"videos": videos}),
            _game(id=2, name="Gone", missing_from_fs=True),
        ]
        seen = []
        monkeypatch.setattr(video_handler, "prefetch_in_background", lambda games: seen.append(list(games)) or True)

        scrape_handler.scrape_library(1)

        assert seen == [[(1, "Alpha", videos)]]

    def test_the_whole_library_can_be_warmed_at_start(self, monkeypatch):
        from handler import video_handler

        seen = []
        monkeypatch.setattr(video_handler, "prefetch_in_background", lambda games: seen.append(list(games)))
        monkeypatch.setattr(
            scrape_handler.db_game_handler,
            "get_all_games",
            lambda: [_game(id=1, name="Here"), _game(id=2, name="Gone", missing_from_fs=True)],
        )
        scrape_handler.warm_all_videos()
        assert seen == [[(1, "Here", None)]]

    def test_a_failure_to_start_the_lookups_never_fails_the_scrape(self, monkeypatch):
        from handler import video_handler

        def boom(games):
            raise RuntimeError("no thread")

        monkeypatch.setattr(video_handler, "prefetch_in_background", boom)
        scrape_handler.warm_videos([_game()])
