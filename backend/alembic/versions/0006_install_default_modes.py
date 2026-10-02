"""add install_default_auto_mode and install_default_manual_mode to settings

Revision ID: 0006_install_default_modes
Revises: 0005_user_avatar_and_hidden_libraries
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_install_default_modes"
down_revision: str | None = "0005_user_avatar_and_hidden_libraries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("install_default_auto_mode", sa.Boolean(), nullable=True))
    op.add_column("settings", sa.Column("install_default_manual_mode", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "install_default_manual_mode")
    op.drop_column("settings", "install_default_auto_mode")
