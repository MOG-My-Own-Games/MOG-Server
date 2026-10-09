import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from endpoints import games
from handler import sizes


@pytest.fixture
def world(monkeypatch, tmp_path: Path):
    """One game folder, one install cache and some saves, with the database stood in for."""
    (tmp_path / "Game" / "data").mkdir(parents=True)
    (tmp_path / "Game" / "setup.exe").write_bytes(b"x" * 1000)
    (tmp_path / "Game" / "data" / "pack.bin").write_bytes(b"y" * 2500)
    cache = tmp_path / "cache" / "9"
    cache.mkdir(parents=True)
    (cache / "files.bin").write_bytes(b"z" * 400)
    game = SimpleNamespace(id=1, fs_name="Game", size_bytes=None, library=SimpleNamespace(root_path=str(tmp_path)))
    state = SimpleNamespace(saves=70, stored=[])
    monkeypatch.setattr(
        sizes, "db_game_handler", SimpleNamespace(update_game=lambda gid, data: state.stored.append((gid, data)))
    )
    monkeypatch.setattr(sizes, "_remembered", {})
    monkeypatch.setattr(sizes, "session_cache_dir", lambda session_id: tmp_path / "cache" / str(session_id))
    monkeypatch.setattr(
        sizes, "db_install_session_handler", SimpleNamespace(get_sessions_for_game=lambda gid: [SimpleNamespace(id=9)])
    )
    monkeypatch.setattr(sizes, "db_saves_handler", SimpleNamespace(summary=lambda game_id: (2, state.saves)))
    monkeypatch.setattr(games.db_game_handler, "get_game", lambda _id: game)

    async def direct(fn, *args):
        return fn(*args)

    monkeypatch.setattr(games, "run_in_threadpool", direct)
    return SimpleNamespace(game=game, state=state, tmp=tmp_path)


def test_size_sums_every_file_under_the_game_folder(world):
    result = asyncio.run(games.get_game_size(SimpleNamespace(), 1))

    assert (result.size_bytes, result.file_count) == (3500, 2)
    assert world.state.stored == [(1, {"size_bytes": 3500})]


def test_sizes_split_the_folder_the_caches_and_the_saves(world):
    result = asyncio.run(games.get_game_sizes(SimpleNamespace(), 1))

    assert (result.installer_bytes, result.cache_bytes, result.saves_bytes, result.total_bytes) == (3500, 400, 70, 3970)


def test_a_size_is_remembered_until_the_top_of_the_folder_changes(world, monkeypatch):
    first = sizes.game_sizes(world.game)
    monkeypatch.setattr(sizes.fs_game_handler, "list_game_files_flat", lambda game: pytest.fail("walked again"))
    assert sizes.game_sizes(world.game) == first

    world.state.saves = 90  # saves are always live
    assert sizes.game_sizes(world.game).saves == 90


def test_a_change_at_the_top_of_the_folder_or_of_a_cache_is_seen(world):
    assert sizes.game_sizes(world.game).installer == 3500
    (world.tmp / "Game" / "patch.exe").write_bytes(b"p" * 100)
    assert sizes.game_sizes(world.game).installer == 3600

    (world.tmp / "cache" / "9" / "more.bin").write_bytes(b"m" * 50)
    assert sizes.game_sizes(world.game).cache == 450


def test_a_size_past_four_gigabytes_is_not_wrapped(monkeypatch, world):
    big = SimpleNamespace(path="a.iso", size_bytes=3_677_174_794)
    monkeypatch.setattr(sizes.fs_game_handler, "list_game_files_flat", lambda game: [big])
    assert sizes.game_sizes(world.game).installer == 3_677_174_794
