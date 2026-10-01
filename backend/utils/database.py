from __future__ import annotations

from typing import Any

import sqlalchemy as sa


def CustomJSON(**kwargs: Any) -> sa.JSON:
    """Plain JSON column. SQLite-only for now, so no per-dialect variant is needed
    (see RomM's utils/database.py, which adds a JSONB variant for PostgreSQL)."""
    return sa.JSON(**kwargs)
