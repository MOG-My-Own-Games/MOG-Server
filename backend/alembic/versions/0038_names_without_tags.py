"""a game with no match is named without the tags of its folder ("Name [Group] [English Translation]" is "Name")

Revision ID: 0038_names_without_tags
Revises: 0037_romm_token
Create Date: 2026-11-01

"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_names_without_tags"
down_revision: str | None = "0037_romm_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A copy of handler/filesystem/fs_tags.name_without_tags: a migration keeps working when the code it came from changes.
_TAG = re.compile(r"\(([^)]+)\)|\[([^\]]+)\]")
_EXT = re.compile(r"\.(exe|iso|zip|rar|7z|tar|gz|sh|run|bin|msi|appimage|pkg|deb|dmg|cue|img|mdf|nrg|chd)$", re.IGNORECASE)


def _name(fs_name: str) -> str:
    return " ".join(_TAG.sub(" ", _EXT.sub("", fs_name)).split()) or fs_name


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, fs_name FROM games WHERE name = fs_name AND igdb_id IS NULL AND metadata_source IS NULL")
    ).fetchall()
    for game_id, fs_name in rows:
        name = _name(fs_name)
        if name != fs_name:
            bind.execute(sa.text("UPDATE games SET name = :name WHERE id = :id"), {"name": name, "id": game_id})


def downgrade() -> None:
    pass  # the names are not given back their tags: fs_name has them
