"""add the installer order kept per game (the installers a chained install runs, first one the game)

Revision ID: 0024_install_steps
Revises: 0023_settings_backups
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_install_steps"
down_revision: str | None = "0023_settings_backups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "install_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("source_path", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_install_steps_game_position", "install_steps", ["game_id", "position"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_install_steps_game_position", table_name="install_steps")
    op.drop_table("install_steps")
