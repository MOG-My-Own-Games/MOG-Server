"""add media to games

Revision ID: 0010_game_media
Revises: 0009_download_workers
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_game_media"
down_revision: str | None = "0009_download_workers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("media", sa.JSON(), nullable=True))
    # What each game already shows as its cover becomes its chosen cover.
    op.execute(
        "UPDATE games SET media = '{}' WHERE media IS NULL"
    )


def downgrade() -> None:
    op.drop_column("games", "media")
