"""add HowLongToBeat id and times to games

Revision ID: 0014_game_hltb
Revises: 0013_install_extract_only
Create Date: 2026-10-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_game_hltb"
down_revision: str | None = "0013_install_extract_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("hltb_id", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("hltb_metadata", sa.JSON(), nullable=True))
    op.execute("UPDATE games SET hltb_metadata = '{}' WHERE hltb_metadata IS NULL")


def downgrade() -> None:
    op.drop_column("games", "hltb_metadata")
    op.drop_column("games", "hltb_id")
