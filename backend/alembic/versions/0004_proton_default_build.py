"""add install_default_proton_build to settings

Revision ID: 0004_proton_default_build
Revises: 0003_vnc_token_and_cache_ttl
Create Date: 2026-10-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_proton_default_build"
down_revision: str | None = "0003_vnc_token_and_cache_ttl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("install_default_proton_build", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("settings", "install_default_proton_build")
