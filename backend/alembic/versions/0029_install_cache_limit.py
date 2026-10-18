"""add the installer cache limit, its auto-clean switch and a warning on the install session

Revision ID: 0029_install_cache_limit
Revises: 0028_launch_args_tags
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_install_cache_limit"
down_revision: str | None = "0028_launch_args_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("install_cache_max_bytes", sa.BigInteger(), nullable=True))
    op.add_column("settings", sa.Column("install_cache_auto_clean", sa.Boolean(), nullable=True))
    op.add_column("install_sessions", sa.Column("warning", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("install_sessions", "warning")
    op.drop_column("settings", "install_cache_auto_clean")
    op.drop_column("settings", "install_cache_max_bytes")
