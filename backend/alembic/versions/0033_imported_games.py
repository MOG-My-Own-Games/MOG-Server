"""let a game exist without a library folder: the games a client imported to sync their saves

Revision ID: 0033_imported_games
Revises: 0032_game_play_sessions
Create Date: 2026-10-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_imported_games"
down_revision: str | None = "0032_game_play_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("imported", sa.Boolean(), nullable=False, server_default="0"))
        batch.alter_column("library_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # An imported game has no library to go back to: it goes, with its saves rows.
    op.execute("DELETE FROM save_versions WHERE game_id IN (SELECT id FROM games WHERE library_id IS NULL)")
    op.execute("DELETE FROM games WHERE library_id IS NULL")
    with op.batch_alter_table("games") as batch:
        batch.alter_column("library_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("imported")
