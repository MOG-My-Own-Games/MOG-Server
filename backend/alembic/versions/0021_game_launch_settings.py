"""add the launch settings kept per game (Proton build, winecfg changes)

Revision ID: 0021_game_launch_settings
Revises: 0020_settings_debug_logging
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_game_launch_settings"
down_revision: str | None = "0020_settings_debug_logging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_launch_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=True),
        sa.Column("proton", sa.String(length=200), nullable=True),
        sa.Column("winecfg_reg", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_game_launch_settings_game_device", "game_launch_settings", ["game_id", "device_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_game_launch_settings_game_device", table_name="game_launch_settings")
    op.drop_table("game_launch_settings")