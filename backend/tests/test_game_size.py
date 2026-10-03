import asyncio
from pathlib import Path
from types import SimpleNamespace

from endpoints import games


def test_size_sums_every_file_under_the_game_folder(monkeypatch, tmp_path: Path):
    (tmp_path / "Game" / "data").mkdir(parents=True)
    (tmp_path / "Game" / "setup.exe").write_bytes(b"x" * 1000)
    (tmp_path / "Game" / "data" / "pack.bin").write_bytes(b"y" * 2500)
    game = SimpleNamespace(id=1, fs_name="Game", library=SimpleNamespace(root_path=str(tmp_path)))
    monkeypatch.setattr(games.db_game_handler, "get_game", lambda _id: game)

    async def direct(fn, *args):
        return fn(*args)

    monkeypatch.setattr(games, "run_in_threadpool", direct)

    result = asyncio.run(games.get_game_size(SimpleNamespace(), 1))

    assert (result.size_bytes, result.file_count) == (3500, 2)
