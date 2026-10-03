"""add missing_from_fs to games

Revision ID: 0007_game_missing_from_fs
Revises: 0006_install_default_modes
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_game_missing_from_fs"
down_revision: str | None = "0006_install_default_modes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("missing_from_fs", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("games", "missing_from_fs")
