"""add the game's size on disk, for sorting by it

Revision ID: 0017_game_size
Revises: 0016_watch_libraries
Create Date: 2026-10-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_game_size"
down_revision: str | None = "0016_watch_libraries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("size_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "size_bytes")
