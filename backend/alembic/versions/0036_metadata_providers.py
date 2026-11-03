"""metadata from more providers: each game's ids per provider and its metadata source, and the settings of the new ones

Revision ID: 0036_metadata_providers
Revises: 0035_provider_misses
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_metadata_providers"
down_revision: str | None = "0035_provider_misses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SWITCHES = ("romm_enabled", "vndb_enabled", "dlsite_enabled", "steam_enabled", "itch_enabled")
TEXTS = (("romm_url", 500), ("romm_username", 255), ("romm_password", 255))


def upgrade() -> None:
    op.add_column("games", sa.Column("provider_ids", sa.JSON(), nullable=True))
    op.add_column("games", sa.Column("metadata_source", sa.String(length=20), nullable=True))
    op.execute("UPDATE games SET provider_ids = '{}' WHERE provider_ids IS NULL")
    op.execute("UPDATE games SET provider_ids = json_object('igdb', igdb_id), metadata_source = 'igdb' WHERE igdb_id IS NOT NULL")
    for name in SWITCHES:
        op.add_column("settings", sa.Column(name, sa.Boolean(), nullable=True))
    for name, length in TEXTS:
        op.add_column("settings", sa.Column(name, sa.String(length=length), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(TEXTS):
        op.drop_column("settings", name)
    for name in reversed(SWITCHES):
        op.drop_column("settings", name)
    op.drop_column("games", "metadata_source")
    op.drop_column("games", "provider_ids")
