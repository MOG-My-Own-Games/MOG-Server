import zipfile
from pathlib import Path

import pytest

from handler import addons
from handler.install import runner


@pytest.fixture
def store(db, tmp_path, monkeypatch):
    base = tmp_path / "addons"
    monkeypatch.setattr(addons, "ADDONS_PATH", str(base))
    return base


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_a_name_is_reduced_to_a_plain_file_name():
    assert addons.sanitize_name("../../etc/Patch 1.0?.zip") == "Patch 1.0_.zip"
    assert addons.sanitize_name("C:\\games\\dlc") == "dlc"


def test_a_name_with_nothing_left_is_refused():
    with pytest.raises(addons.AddonError):
        addons.sanitize_name("...")


def test_only_the_three_categories_are_accepted():
    assert addons.check_category("patch") == "patch"
    with pytest.raises(addons.AddonError):
        addons.check_category("save")


def test_an_entity_keeps_its_zip_in_a_folder_of_its_own_and_counts_its_files(store, tmp_path):
    source = _zip(tmp_path / "in.zip", {"Game/a.pak": b"x" * 10, "Game/b.dll": b"y" * 5})

    row = addons.add_zip(3, "mod", "Better Textures", source, user_id=1, session_id=None)

    assert (row.size_bytes, row.file_count, row.category, row.name) == (15, 2, "mod", "Better Textures")
    assert addons.content_path(row) == store / "3" / "mod" / str(row.id) / "content.zip"
    assert addons.content_path(row).is_file()


def test_a_zip_that_points_outside_the_game_is_refused_and_nothing_is_kept(store, tmp_path):
    source = _zip(tmp_path / "evil.zip", {"../../outside.txt": b"x"})

    with pytest.raises(addons.AddonError):
        addons.add_zip(3, "patch", "evil", source, user_id=1)

    assert not (store / "3").exists()


def test_a_file_that_is_not_a_zip_is_refused(store, tmp_path):
    source = tmp_path / "not.zip"
    source.write_bytes(b"plain text")

    with pytest.raises(addons.AddonError):
        addons.add_zip(3, "dlc", "x", source, user_id=1)


def test_removing_an_entity_removes_its_row_and_its_folder(store, tmp_path):
    source = _zip(tmp_path / "in.zip", {"a.pak": b"x"})
    row = addons.add_zip(3, "patch", "Patch 2.0", source, user_id=1)

    addons.remove(row)

    assert not addons.addon_dir(3, "patch", row.id).exists()
    from handler.database import db_game_addons_handler

    assert db_game_addons_handler.get(row.id) is None


def test_a_build_is_kept_only_when_asked_for(store, tmp_path):
    a = tmp_path / "a.bin"
    a.write_bytes(b"1")
    built = addons.build_zip([("drive_c/Game/a.bin", a)])

    assert zipfile.ZipFile(built).namelist() == ["drive_c/Game/a.bin"]
    addons.discard_zip(built)
    assert not built.exists()


def test_a_changed_file_is_the_only_one_a_patch_run_keeps(tmp_path):
    drive_c = tmp_path / "drive_c"
    game = drive_c / "Game"
    game.mkdir(parents=True)
    old = game / "old.dat"
    changed = game / "changed.dat"
    old.write_bytes(b"same")
    changed.write_bytes(b"before")
    before = runner._file_stats([old, changed])
    changed.write_bytes(b"after, and longer")
    new = game / "new.dat"
    new.write_bytes(b"new")

    found = runner._changed_since(before, tmp_path, frozenset(), drive_c)

    assert sorted(member for member, _ in found) == ["Game/changed.dat", "Game/new.dat"]
