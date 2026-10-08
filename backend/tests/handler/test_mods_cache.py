import os
import threading
import zipfile

import pytest

from handler import mods


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "MODS_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(mods, "_jobs", {})
    return tmp_path / "cache"


def _zip(cache, game_id, name, data=b"z"):
    path = cache / str(game_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_finished_zips_are_listed_by_game_and_unfinished_or_stray_files_are_not(cache):
    _zip(cache, 12, "b.zip", b"bb")
    _zip(cache, 3, "a.zip", b"a")
    _zip(cache, 3, "half.zip.part")
    _zip(cache, 3, "notes.txt")
    _zip(cache, "tmp", "x.zip")

    found = mods.cached_zips()

    assert [(z.game_id, z.file_name, z.size_bytes) for z in found] == [(3, "a.zip", 1), (12, "b.zip", 2)]
    assert found[0].modified_at == pytest.approx(os.path.getmtime(cache / "3" / "a.zip"))


def test_no_cache_folder_means_no_zips(cache):
    assert mods.cached_zips() == []


def test_a_zip_is_removed_by_game_and_exact_name_only(cache):
    kept = _zip(cache, 3, "keep.zip")
    gone = _zip(cache, 3, "gone.zip")
    other = _zip(cache, 4, "gone.zip")

    assert mods.remove_cached_zip(3, "gone.zip") is True
    assert not gone.exists() and kept.exists() and other.exists()
    assert mods.remove_cached_zip(3, "gone.zip") is False
    for bad in ("../4/gone.zip", "gone", "keep.zip.part", ""):
        assert mods.remove_cached_zip(3, bad) is False
    assert kept.exists() and other.exists()


def test_a_zip_being_made_again_is_left_alone(cache):
    busy = _zip(cache, 3, "mod1.zip")
    mods._jobs[(3, "mod1")] = mods.ZipJob(state="zipping")

    assert mods.remove_cached_zip(3, "mod1.zip") is False
    assert busy.exists()
    assert mods.clear_cached_zips() == 0

    mods._jobs[(3, "mod1")].state = "ready"
    assert mods.clear_cached_zips() == 1 and not busy.exists()


def test_clearing_counts_what_went_and_a_removed_zip_is_made_again_on_request(cache, tmp_path):
    _zip(cache, 1, "a.zip")
    _zip(cache, 2, "b.zip")
    assert mods.clear_cached_zips() == 2 and mods.cached_zips() == []

    folder = tmp_path / "Mods" / "mod1"
    folder.mkdir(parents=True)
    (folder / "f.txt").write_text("x")
    mod = mods.Mod(name="mod1", path=folder, kind=mods.KIND_FOLDER, size_bytes=1, file_count=1)
    job = mods.start_zip(5, mod, user_id=1)
    for _ in range(500):
        if job.state != "zipping":
            break
        threading.Event().wait(0.01)
    assert job.state == "ready" and [z.file_name for z in mods.cached_zips()] == ["mod1.zip"]
    assert mods.clear_cached_zips() == 1
    again = mods.start_zip(5, mod, user_id=1)
    assert again is not job  # the file was gone, so the old job's zip no longer counts
    for _ in range(500):
        if again.state != "zipping":
            break
        threading.Event().wait(0.01)
    with zipfile.ZipFile(again.path) as z:
        assert z.namelist() == ["mod1/f.txt"]
