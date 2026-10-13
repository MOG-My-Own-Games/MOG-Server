"""add the chained installers of an install session and the step it is on

Revision ID: 0025_install_session_steps
Revises: 0024_install_steps
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_install_session_steps"
down_revision: str | None = "0024_install_steps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("install_sessions") as batch:
        batch.add_column(sa.Column("steps", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("step_index", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("install_sessions") as batch:
        batch.drop_column("step_index")
        batch.drop_column("steps")
