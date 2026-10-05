"""add addons_only to games

Revision ID: 0012_game_addons_only
Revises: 0011_saves
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_game_addons_only"
down_revision: str | None = "0011_saves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("addons_only", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("games", "addons_only")
