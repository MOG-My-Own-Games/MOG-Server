from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import TIMESTAMP, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from starlette.authentication import SimpleUser

from models.base import BaseModel

if TYPE_CHECKING:
    from models.install_session import InstallSession

TEXT_FIELD_LENGTH = 255

# Id of the synthetic, unauthenticated visitor a future kiosk mode could hand
# out. Reserved now so it never collides with a real auto-increment row.
KIOSK_USER_ID: Final = -1


class Role(enum.StrEnum):
    # Two kinds only, same as RomM's current model: admins bypass every
    # permission check; everyone else is a plain user. No granular
    # permission groups yet (see docs/TODO.md).
    USER = "user"
    ADMIN = "admin"


class User(BaseModel, SimpleUser):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        String(length=TEXT_FIELD_LENGTH), unique=True, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(length=TEXT_FIELD_LENGTH))
    enabled: Mapped[bool] = mapped_column(default=True)
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=Role.USER,
    )
    last_login: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    install_sessions: Mapped[list["InstallSession"]] = relationship(
        lazy="select", back_populates="user"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN
