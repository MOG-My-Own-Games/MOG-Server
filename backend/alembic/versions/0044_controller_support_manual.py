"""add controller_support_manual to games

Revision ID: 0044_controller_support_manual
Revises: 0043_controller_support_enabled
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_controller_support_manual"
down_revision: str | None = "0043_controller_support_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("controller_support_manual", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("games", "controller_support_manual")
