from unittest.mock import patch

from handler.scan_handler import scan_library
from models.game import Game
from models.library import Library


def _game(id: int, fs_name: str, missing: bool = False) -> Game:
    return Game(id=id, library_id=1, fs_name=fs_name, name=fs_name, missing_from_fs=missing)


@patch("handler.scan_handler.db_game_handler")
class TestScanLibrary:
    def test_flags_vanished_game_instead_of_deleting(self, db, tmp_path):
        (tmp_path / "Here").mkdir()
        db.get_games_for_library.return_value = [_game(1, "Here"), _game(2, "Gone")]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        db.delete_game.assert_not_called()
        db.update_game.assert_called_once_with(2, {"missing_from_fs": True})
        assert (result.added, result.missing, result.total) == (0, 1, 1)

    def test_clears_flag_when_game_reappears(self, db, tmp_path):
        (tmp_path / "Back").mkdir()
        db.get_games_for_library.return_value = [_game(1, "Back", missing=True)]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        db.update_game.assert_called_once_with(1, {"missing_from_fs": False})
        assert result.missing == 0

    def test_unreadable_root_flags_everything(self, db, tmp_path):
        db.get_games_for_library.return_value = [_game(1, "A")]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path / "nope")))

        assert result.missing == 1
