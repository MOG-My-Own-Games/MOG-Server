"""add install_enable_network to settings

Revision ID: 0041_install_enable_network
Revises: 0040_save_rules
Create Date: 2026-11-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_install_enable_network"
down_revision: str | None = "0040_save_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("install_enable_network", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "install_enable_network")
