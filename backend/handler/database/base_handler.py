from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Result, create_engine
from sqlalchemy.orm import sessionmaker

from config import SQLITE_PATH

sync_engine = create_engine(f"sqlite:///{SQLITE_PATH}", connect_args={"check_same_thread": False})
sync_session = sessionmaker(bind=sync_engine, expire_on_commit=False)

logging.getLogger("sqlalchemy.engine.Engine").handlers = [logging.NullHandler()]


class DBBaseHandler: ...


def affected_rows(result: Result[Any]) -> int:
    """How many rows an UPDATE or DELETE run through `Session.execute` matched."""
    return result.rowcount  # type: ignore[attr-defined]
