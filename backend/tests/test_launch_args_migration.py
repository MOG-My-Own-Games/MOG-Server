import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).parent.parent


def _alembic(db: Path, *args: str) -> None:
    env = {**os.environ, "SQLITE_PATH": str(db), "MOG_BASE_PATH": str(db.parent)}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True)


def test_launch_arguments_typed_as_text_become_tags_and_go_back_to_text(tmp_path):
    db = tmp_path / "mog.db"
    _alembic(db, "upgrade", "0027_launch_settings_args")
    with sqlite3.connect(db) as conn:
        for game_id, text in ((1, "-intro:off -res 1280 720 --nosound"), (2, None)):
            conn.execute(
                "insert into game_launch_settings (game_id, device_id, launch_args, created_at, updated_at)"
                " values (?, NULL, ?, datetime('now'), datetime('now'))",
                (game_id, text),
            )

    _alembic(db, "upgrade", "0028_launch_args_tags")
    with sqlite3.connect(db) as conn:
        rows = conn.execute("select game_id, launch_args from game_launch_settings order by game_id").fetchall()
    assert [(g, json.loads(t) if t else None) for g, t in rows] == [(1, ["-intro:off", "-res 1280 720", "--nosound"]), (2, None)]

    _alembic(db, "downgrade", "0027_launch_settings_args")
    with sqlite3.connect(db) as conn:
        assert conn.execute("select launch_args from game_launch_settings where game_id = 1").fetchone() == ("-intro:off -res 1280 720 --nosound",)
