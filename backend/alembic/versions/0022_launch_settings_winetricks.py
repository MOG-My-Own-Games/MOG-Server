"""add the winetricks verbs to the launch settings

Revision ID: 0022_launch_settings_winetricks
Revises: 0021_game_launch_settings
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_launch_settings_winetricks"
down_revision: str | None = "0021_game_launch_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_launch_settings", sa.Column("winetricks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("game_launch_settings", "winetricks")