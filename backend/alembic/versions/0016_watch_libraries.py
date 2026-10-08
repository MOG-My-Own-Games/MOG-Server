"""add the library-watching switch to settings

Revision ID: 0016_watch_libraries
Revises: 0015_provider_toggles
Create Date: 2026-10-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_watch_libraries"
down_revision: str | None = "0015_provider_toggles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("watch_libraries", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "watch_libraries")
