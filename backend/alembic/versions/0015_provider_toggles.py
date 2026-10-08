"""add a per-provider on/off switch to settings

Revision ID: 0015_provider_toggles
Revises: 0014_game_hltb
Create Date: 2026-10-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_provider_toggles"
down_revision: str | None = "0014_game_hltb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("igdb_enabled", "steamgriddb_enabled", "hltb_enabled")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("settings", sa.Column(name, sa.Boolean(), nullable=True))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("settings", name)
