"""add the play sessions, the time each user played each game

Revision ID: 0032_game_play_sessions
Revises: 0031_launch_settings_executable
Create Date: 2026-10-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_game_play_sessions"
down_revision: str | None = "0031_launch_settings_executable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_play_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "game_id", "started_at", name="uq_game_play_sessions_start"),
    )
    op.create_index("ix_game_play_sessions_user", "game_play_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_game_play_sessions_user", table_name="game_play_sessions")
    op.drop_table("game_play_sessions")
