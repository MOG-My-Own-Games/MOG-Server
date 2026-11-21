"""add controller_support to games

Revision ID: 0042_controller_support
Revises: 0041_install_enable_network
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_controller_support"
down_revision: str | None = "0041_install_enable_network"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("controller_support", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "controller_support")
