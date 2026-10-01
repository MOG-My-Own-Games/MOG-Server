"""add vnc_token and install_cache_ttl_days

Revision ID: 0003_vnc_token_and_cache_ttl
Revises: 0002_settings
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_vnc_token_and_cache_ttl"
down_revision: str | None = "0002_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("install_sessions", sa.Column("vnc_token", sa.String(length=64), nullable=True))
    op.add_column("settings", sa.Column("install_cache_ttl_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "install_cache_ttl_days")
    op.drop_column("install_sessions", "vnc_token")
