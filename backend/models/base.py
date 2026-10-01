from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BaseModel(DeclarativeBase):
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), default=utc_now, onupdate=utc_now
    )
