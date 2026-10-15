"""keep the launch arguments as a list of tags

Revision ID: 0028_launch_args_tags
Revises: 0027_launch_settings_args
Create Date: 2026-10-14

"""

import json
import re
import shlex
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_launch_args_tags"
down_revision: str | None = "0027_launch_settings_args"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEGATIVE_NUMBER = re.compile(r"-\d+(?:\.\d+)?")


def _split_tags(text: str) -> list[str]:
    """The same as utils.launch_args.split_tags, kept here so the migration does not change with the code."""
    try:
        words = shlex.split(text)
    except ValueError:
        words = text.split()
    tags: list[str] = []
    group: list[str] = []
    for word in words:
        if word.startswith("-") and not _NEGATIVE_NUMBER.fullmatch(word) and group:
            tags.append(shlex.join(group))
            group = []
        group.append(word)
    if group:
        tags.append(shlex.join(group))
    return list(dict.fromkeys(tags))


def upgrade() -> None:
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.add_column(sa.Column("launch_tags", sa.JSON(), nullable=True))
    conn = op.get_bind()
    for row_id, text in conn.execute(sa.text("SELECT id, launch_args FROM game_launch_settings WHERE launch_args IS NOT NULL")).fetchall():
        conn.execute(
            sa.text("UPDATE game_launch_settings SET launch_tags = :tags WHERE id = :id"),
            {"tags": json.dumps(_split_tags(text)), "id": row_id},
        )
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.drop_column("launch_args")
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.alter_column("launch_tags", new_column_name="launch_args")


def downgrade() -> None:
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.add_column(sa.Column("launch_text", sa.String(length=1000), nullable=True))
    conn = op.get_bind()
    for row_id, tags in conn.execute(sa.text("SELECT id, launch_args FROM game_launch_settings WHERE launch_args IS NOT NULL")).fetchall():
        tags = json.loads(tags) if isinstance(tags, str) else tags
        conn.execute(
            sa.text("UPDATE game_launch_settings SET launch_text = :text WHERE id = :id"),
            {"text": " ".join(tags)[:1000], "id": row_id},
        )
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.drop_column("launch_args")
    with op.batch_alter_table("game_launch_settings") as batch:
        batch.alter_column("launch_text", new_column_name="launch_args")
