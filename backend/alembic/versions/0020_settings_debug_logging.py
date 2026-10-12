"""add the debug logging switch to the settings

Revision ID: 0020_settings_debug_logging
Revises: 0019_game_has_mods
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_settings_debug_logging"
down_revision: str | None = "0019_game_has_mods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("debug_logging", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "debug_logging")