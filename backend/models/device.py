"""A machine a user runs the MOG client on. Its saves are filed under it, so each upload shows
which machine it came from and each machine keeps its own history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

NAME_MAX_LENGTH = 64
CLIENT_UID_MAX_LENGTH = 64
HOSTNAME_MAX_LENGTH = 255
PLATFORM_MAX_LENGTH = 32


class Device(BaseModel):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "client_uid", name="uq_devices_user_client_uid"),
        UniqueConstraint("user_id", "name", name="uq_devices_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Generated once by the client and kept in its config; it is what tells two machines with
    # the same hostname apart.
    client_uid: Mapped[str] = mapped_column(String(length=CLIENT_UID_MAX_LENGTH))
    name: Mapped[str] = mapped_column(String(length=NAME_MAX_LENGTH))
    hostname: Mapped[str | None] = mapped_column(String(length=HOSTNAME_MAX_LENGTH), default=None)
    platform: Mapped[str | None] = mapped_column(String(length=PLATFORM_MAX_LENGTH), default=None)
    last_seen: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    def __repr__(self) -> str:
        return f"Device({self.id} {self.name!r})"
