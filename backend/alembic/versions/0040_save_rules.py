"""add game_save_rules

Revision ID: 0040_save_rules
Revises: 0039_media_cleared
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_save_rules"
down_revision: str | None = "0039_media_cleared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_save_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_game_save_rules_game_path", "game_save_rules", ["game_id", "path"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_game_save_rules_game_path", table_name="game_save_rules")
    op.drop_table("game_save_rules")
