"""remember which metadata providers found nothing for a game, so an automatic pass does not search again

Revision ID: 0035_provider_misses
Revises: 0034_game_title_key
Create Date: 2026-10-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_provider_misses"
down_revision: str | None = "0034_game_title_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("provider_misses", sa.JSON(), nullable=True))
    op.execute("UPDATE games SET provider_misses = '{}' WHERE provider_misses IS NULL")


def downgrade() -> None:
    op.drop_column("games", "provider_misses")
