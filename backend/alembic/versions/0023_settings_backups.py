"""add the backup switches to the settings

Revision ID: 0023_settings_backups
Revises: 0022_launch_settings_winetricks
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_settings_backups"
down_revision: str | None = "0022_launch_settings_winetricks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("backup_daily", sa.Boolean(), nullable=True))
    op.add_column("settings", sa.Column("backup_saves", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "backup_saves")
    op.drop_column("settings", "backup_daily")
