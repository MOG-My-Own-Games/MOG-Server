"""add whether the game has mods, for filtering by it

Revision ID: 0019_game_has_mods
Revises: 0018_install_queue_rank
Create Date: 2026-10-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_game_has_mods"
down_revision: str | None = "0018_install_queue_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("has_mods", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("games", "has_mods")
