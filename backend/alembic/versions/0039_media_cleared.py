"""remember the artwork kinds a person removed on purpose, so an automatic scrape does not put them back

Revision ID: 0039_media_cleared
Revises: 0038_names_without_tags
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_media_cleared"
down_revision: str | None = "0038_names_without_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("games", sa.Column("media_cleared", sa.JSON(), nullable=True))
    op.execute("UPDATE games SET media_cleared = '[]' WHERE media_cleared IS NULL")


def downgrade() -> None:
    op.drop_column("games", "media_cleared")
