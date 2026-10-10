"""add the place in the install queue

Revision ID: 0018_install_queue_rank
Revises: 0017_game_size
Create Date: 2026-10-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_install_queue_rank"
down_revision: str | None = "0017_game_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("install_sessions", sa.Column("queue_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("install_sessions", "queue_rank")
