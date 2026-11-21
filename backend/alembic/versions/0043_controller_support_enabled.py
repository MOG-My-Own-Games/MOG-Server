"""add controller_support_enabled to settings

Revision ID: 0043_controller_support_enabled
Revises: 0042_controller_support
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_controller_support_enabled"
down_revision: str | None = "0042_controller_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("controller_support_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "controller_support_enabled")
