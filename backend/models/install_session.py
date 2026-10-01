# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel

if TYPE_CHECKING:
    from models.game import Game
    from models.user import User


class InstallSessionState(enum.StrEnum):
    # Detecting installer candidates in the game's files.
    DETECTING = "detecting"
    # No installer could be auto-detected; waiting for the client to pick one.
    AWAITING_INSTALLER = "awaiting_installer"
    # Sandbox is running the installer (X/VNC available).
    INSTALLING = "installing"
    # Installer finished; installed files are being streamed to clients.
    STREAMING = "streaming"
    # Install completed and all files streamed.
    DONE = "done"
    # Install failed (see `error`).
    FAILED = "failed"
    # Cache TTL elapsed and files were evicted.
    EXPIRED = "expired"


class InstallPhase(enum.StrEnum):
    # Unpacking an archive (.zip/.7z/...) that holds the installer.
    EXTRACTING = "extracting"
    # Reading a disc image (.iso/.chd/...) that holds the installer.
    MOUNTING = "mounting"


# States in which the install is still doing work and should not be restarted.
ACTIVE_INSTALL_STATES = frozenset(
    {
        InstallSessionState.DETECTING,
        InstallSessionState.AWAITING_INSTALLER,
        InstallSessionState.INSTALLING,
        InstallSessionState.STREAMING,
    }
)

# States that actually hold sandbox/VNC/disk resources, as opposed to just
# waiting on user input. Used to bound concurrency (INSTALL_MAX_CONCURRENCY).
RUNNING_INSTALL_STATES = frozenset(
    {
        InstallSessionState.INSTALLING,
        InstallSessionState.STREAMING,
    }
)


class InstallSession(BaseModel):
    __tablename__ = "install_sessions"
    __table_args__ = (
        Index("ix_install_sessions_game", "game_id"),
        Index("ix_install_sessions_user", "user_id"),
        Index("ix_install_sessions_state", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    state: Mapped[InstallSessionState] = mapped_column(
        Enum(
            InstallSessionState,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=InstallSessionState.DETECTING,
        nullable=False,
    )

    # Path (relative to the game's directory) of the installer chosen for this run.
    installer_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    # Archive or disc image (relative to the game's directory) the installer is
    # extracted from. NULL when the installer is run straight from the game's
    # files; then `installer_path` is relative to the extracted tree instead.
    source_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    # Proton build id chosen for this run (see handler.install.proton_builds).
    # NULL falls back to the first installed build the manager discovers.
    proton_build: Mapped[str | None] = mapped_column(String(255), default=None)
    # Absolute path of this session's working directory under INSTALL_CACHE_PATH.
    cache_path: Mapped[str | None] = mapped_column(String(1000), default=None)

    # When the cache should be evicted. NULL means "unlimited" (never auto-evict).
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    # What the runner is doing before the installer window exists (see
    # InstallPhase) and the file it is doing it to. NULL otherwise.
    phase: Mapped[str | None] = mapped_column(String(32), default=None)
    phase_detail: Mapped[str | None] = mapped_column(String(1000), default=None)

    # Experimental auto mode: OCR the installer and press its buttons. Read by
    # the runner every tick, so it can be flipped while the installer runs.
    auto_mode: Mapped[bool] = mapped_column(
        Boolean(), default=False, server_default=sa_false(), nullable=False
    )
    # Per-install manual mode override: forces AWAITING_INSTALLER even when
    # candidates exist, so the user can pick the installer themselves.
    manual_mode: Mapped[bool] = mapped_column(
        Boolean(), default=False, server_default=sa_false(), nullable=False
    )
    # What auto mode is doing ("running" / "needs_manual", see
    # handler.install.auto_mode.driver) and its last action. NULL when off.
    auto_status: Mapped[str | None] = mapped_column(String(32), default=None)
    auto_detail: Mapped[str | None] = mapped_column(String(1000), default=None)

    # noVNC URL for the running installer; only set while state == INSTALLING.
    vnc_url: Mapped[str | None] = mapped_column(String(500), default=None)
    # Leased websocket (web-facing) port backing vnc_url, kept alongside it so
    # the VNC proxy's ownership check can match "does this user own a running
    # session on this port" without re-parsing vnc_url.
    vnc_web_port: Mapped[int | None] = mapped_column(Integer, default=None)
    # Random per-session capability token, embedded in vnc_url itself (both
    # the page URL and the websocket path it hands noVNC). The VNC proxy
    # routes check this instead of the usual Basic-auth user, so the page can
    # be embedded in an <iframe>: a real browser login prompt only ever
    # fires for a top-level navigation, never for an iframe's own request,
    # so Basic auth alone would leave an embedded view stuck unauthenticated.
    vnc_token: Mapped[str | None] = mapped_column(String(64), default=None)

    # Live progress of the streamed install.
    bytes_written: Mapped[int] = mapped_column(BigInteger(), default=0, nullable=False)
    bytes_total: Mapped[int] = mapped_column(BigInteger(), default=0, nullable=False)

    error: Mapped[str | None] = mapped_column(Text, default=None)

    # Plain lazy loading for Phase 1 simplicity (see models/game.py's own note).
    game: Mapped["Game"] = relationship(lazy="joined", back_populates="install_sessions")
    user: Mapped["User"] = relationship(lazy="select", back_populates="install_sessions")

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_INSTALL_STATES

    @property
    def is_unlimited(self) -> bool:
        return self.expires_at is None and self.state != InstallSessionState.EXPIRED

    def __repr__(self) -> str:
        return (
            f"InstallSession({self.id} game={self.game_id} "
            f"user={self.user_id} state={self.state})"
        )
