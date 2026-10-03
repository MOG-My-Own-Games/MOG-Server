"""add download_workers to settings

Revision ID: 0009_download_workers
Revises: 0008_notifications
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_download_workers"
down_revision: str | None = "0008_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("download_workers", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "download_workers")
