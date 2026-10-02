"""add user avatar_path and hidden_library_ids

Revision ID: 0005_user_avatar_and_hidden_libraries
Revises: 0004_proton_default_build
Create Date: 2026-10-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_user_avatar_and_hidden_libraries"
down_revision: str | None = "0004_proton_default_build"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_path", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column("hidden_library_ids", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("users", "hidden_library_ids")
    op.drop_column("users", "avatar_path")
