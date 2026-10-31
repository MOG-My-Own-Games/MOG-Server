"""a game can be tied to a title by hand: versions of one game that the IGDB id does not tell

Revision ID: 0034_game_title_key
Revises: 0033_imported_games
Create Date: 2026-10-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_game_title_key"
down_revision: str | None = "0033_imported_games"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("title_key", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_column("title_key")
