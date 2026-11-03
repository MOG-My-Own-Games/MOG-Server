"""RomM is reached with a client API token, not a username and password

Revision ID: 0037_romm_token
Revises: 0036_metadata_providers
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_romm_token"
down_revision: str | None = "0036_metadata_providers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(sa.Column("romm_token", sa.String(length=255), nullable=True))
        batch.drop_column("romm_username")
        batch.drop_column("romm_password")


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch:
        batch.add_column(sa.Column("romm_password", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("romm_username", sa.String(length=255), nullable=True))
        batch.drop_column("romm_token")
