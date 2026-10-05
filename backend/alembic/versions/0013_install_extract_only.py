"""add extract_only to install sessions

Revision ID: 0013_install_extract_only
Revises: 0012_game_addons_only
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_install_extract_only"
down_revision: str | None = "0012_game_addons_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "install_sessions", sa.Column("extract_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("install_sessions", "extract_only")
