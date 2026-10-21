"""add the executable to the launch settings

Revision ID: 0031_launch_settings_executable
Revises: 0030_saves_keep_versions
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_launch_settings_executable"
down_revision: str | None = "0030_saves_keep_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("game_launch_settings", sa.Column("executable", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("game_launch_settings", "executable")
