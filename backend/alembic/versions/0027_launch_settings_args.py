"""add the launch arguments to the launch settings

Revision ID: 0027_launch_settings_args
Revises: 0026_game_addons
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_launch_settings_args"
down_revision: str | None = "0026_game_addons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_launch_settings", sa.Column("launch_args", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("game_launch_settings", "launch_args")
