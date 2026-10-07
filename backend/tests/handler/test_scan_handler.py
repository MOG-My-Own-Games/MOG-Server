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
        (tmp_path / "Here" / "file.bin").write_bytes(b"x")
        db.get_games_for_library.return_value = [_game(1, "Here"), _game(2, "Gone")]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        db.delete_game.assert_not_called()
        db.update_game.assert_called_once_with(2, {"missing_from_fs": True})
        assert (result.added, result.missing, result.total) == (0, 1, 1)

    def test_clears_flag_when_game_reappears(self, db, tmp_path):
        (tmp_path / "Back").mkdir()
        (tmp_path / "Back" / "file.bin").write_bytes(b"x")
        db.get_games_for_library.return_value = [_game(1, "Back", missing=True)]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        db.update_game.assert_called_once_with(1, {"missing_from_fs": False})
        assert result.missing == 0

    def test_unreadable_root_flags_everything(self, db, tmp_path):
        db.get_games_for_library.return_value = [_game(1, "A")]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path / "nope")))

        assert result.missing == 1


class TestEmptyFolders:
    @patch("handler.scan_handler.db_game_handler")
    def test_empty_and_nested_empty_folders_are_not_games(self, db, tmp_path):
        (tmp_path / "Empty").mkdir()
        (tmp_path / "OnlyDirs" / "a" / "b").mkdir(parents=True)
        (tmp_path / "Real").mkdir()
        (tmp_path / "Real" / "setup.exe").write_bytes(b"x")
        (tmp_path / "Loose.iso").write_bytes(b"x")
        db.get_games_for_library.return_value = []

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        added = sorted(c.args[0].fs_name for c in db.add_game.call_args_list)
        assert added == ["Loose.iso", "Real"]
        assert result.total == 2

    @patch("handler.scan_handler.db_game_handler")
    def test_a_known_game_whose_folder_was_emptied_is_flagged_missing(self, db, tmp_path):
        (tmp_path / "Hollow").mkdir()
        db.get_games_for_library.return_value = [_game(1, "Hollow")]

        result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        db.update_game.assert_called_once_with(1, {"missing_from_fs": True})
        assert result.missing == 1


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class TestAddonsOnly:
    def test_a_folder_with_only_dlc_and_mods_is_add_ons_only(self, tmp_path):
        from handler.scan_handler import only_addons

        _write(tmp_path / "Game" / "DLC" / "Expansion" / "setup.exe")
        _write(tmp_path / "Game" / "mods" / "cool.zip")
        _write(tmp_path / "Game" / "readme.txt")
        assert only_addons(tmp_path / "Game") is True

    def test_a_base_game_installer_anywhere_outside_the_add_on_folders_makes_it_a_game(self, tmp_path):
        from handler.scan_handler import only_addons

        _write(tmp_path / "Top" / "setup.exe")
        _write(tmp_path / "Top" / "DLC" / "dlc.exe")
        _write(tmp_path / "Nested" / "Disc 1" / "install.exe")
        _write(tmp_path / "Nested" / "Patch" / "patch.exe")
        _write(tmp_path / "Iso" / "game.iso")
        _write(tmp_path / "Iso" / "Mods" / "m.zip")
        assert [only_addons(tmp_path / n) for n in ("Top", "Nested", "Iso")] == [False, False, False]

    def test_a_game_without_add_on_folders_is_never_flagged_even_with_nothing_to_install(self, tmp_path):
        from handler.scan_handler import only_addons

        _write(tmp_path / "Plain" / "data" / "level.dat")
        _write(tmp_path / "Loose.iso")
        assert only_addons(tmp_path / "Plain") is False and only_addons(tmp_path / "Loose.iso") is False

    def test_a_prerequisite_installer_is_not_the_base_game(self, tmp_path):
        from handler.scan_handler import only_addons

        _write(tmp_path / "G" / "redist" / "vcredist_x64.exe")
        _write(tmp_path / "G" / "DLC" / "extra.dat")
        assert only_addons(tmp_path / "G") is True

    @patch("handler.scan_handler.db_game_handler")
    def test_the_scan_records_the_flag_when_it_appears_and_when_it_goes(self, db, tmp_path):
        _write(tmp_path / "A" / "DLC" / "x.dat")
        _write(tmp_path / "B" / "setup.exe")
        _write(tmp_path / "B" / "DLC" / "x.dat")
        a, b = _game(1, "A"), _game(2, "B")
        b.addons_only = True  # since fixed: the base game was added
        db.get_games_for_library.return_value = [a, b]

        scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        assert sorted(c.args for c in db.update_game.call_args_list) == [(1, {"addons_only": True}), (2, {"addons_only": False})]

    @patch("handler.scan_handler.db_game_handler")
    def test_a_new_game_is_flagged_as_it_is_added(self, db, tmp_path):
        _write(tmp_path / "OnlyMods" / "mods" / "m.zip")
        _write(tmp_path / "Full" / "setup.exe")
        db.get_games_for_library.return_value = []

        scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

        added = {c.args[0].fs_name: c.args[0].addons_only for c in db.add_game.call_args_list}
        assert added == {"OnlyMods": True, "Full": False}


@patch("handler.scan_handler.db_game_handler")
def test_a_scan_lists_the_games_it_added(db, tmp_path):
    for name in ("A", "B"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.bin").write_bytes(b"x")
    db.get_games_for_library.return_value = []
    db.add_game.side_effect = lambda game: Game(id=len(db.add_game.call_args_list) + 40, fs_name=game.fs_name, name=game.name)

    result = scan_library(Library(id=1, name="L", root_path=str(tmp_path)))

    assert result.new_games == ((41, "A"), (42, "B")) and result.added == 2
