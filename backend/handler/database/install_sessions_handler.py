# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.install_session import (
    ACTIVE_INSTALL_STATES,
    RUNNING_INSTALL_STATES,
    InstallSession,
    InstallSessionState,
)

from .base_handler import DBBaseHandler


class DBInstallSessionsHandler(DBBaseHandler):
    @begin_session
    def add_session(
        self, install_session: InstallSession, session: Session = None  # type: ignore
    ) -> InstallSession:
        session.add(install_session)
        session.flush()
        session.refresh(install_session)
        return install_session

    @begin_session
    def get_session(
        self, install_session_id: int, session: Session = None  # type: ignore
    ) -> InstallSession | None:
        return session.get(InstallSession, install_session_id)

    @begin_session
    def get_latest_session_for_game(
        self, game_id: int, user_id: int, session: Session = None  # type: ignore
    ) -> InstallSession | None:
        return session.scalars(
            select(InstallSession)
            .where(InstallSession.game_id == game_id, InstallSession.user_id == user_id)
            .order_by(InstallSession.created_at.desc(), InstallSession.id.desc())
        ).first()

    @begin_session
    def count_running_sessions(self, session: Session = None) -> int:  # type: ignore
        """Sessions actually holding sandbox/VNC resources right now - narrower
        than every ACTIVE state, used to bound INSTALL_MAX_CONCURRENCY."""
        return (
            session.scalar(
                select(func.count(InstallSession.id)).where(
                    InstallSession.state.in_(RUNNING_INSTALL_STATES)
                )
            )
            or 0
        )

    @begin_session
    def get_installing_sessions(self, session: Session = None) -> list[InstallSession]:  # type: ignore
        return list(
            session.scalars(
                select(InstallSession).where(InstallSession.state == InstallSessionState.INSTALLING)
            ).all()
        )

    @begin_session
    def get_running_session_for_port(
        self, vnc_web_port: int, session: Session = None  # type: ignore
    ) -> InstallSession | None:
        """Whatever session's sandbox is currently on this VNC port, any
        owner - backs the VNC proxy's static-asset route, which only needs
        to know "is anything legitimately live on this port" (the actual
        sensitive bits - the VNC password and this session's own vnc_token -
        are never exposed by that check alone, see endpoints/install.py)."""
        return session.scalars(
            select(InstallSession).where(
                InstallSession.vnc_web_port == vnc_web_port,
                InstallSession.state == InstallSessionState.INSTALLING,
            )
        ).first()

    @begin_session
    def update_session(
        self, install_session_id: int, data: dict, session: Session = None  # type: ignore
    ) -> InstallSession | None:
        session.execute(
            update(InstallSession).where(InstallSession.id == install_session_id).values(**data)
        )
        return session.get(InstallSession, install_session_id)

    @begin_session
    def set_expiry_where_unlimited(
        self, expires_at: datetime, session: Session = None  # type: ignore
    ) -> int:
        """Give every cache that never expires this expiry. Returns how many changed."""
        result = session.execute(
            update(InstallSession)
            .where(
                InstallSession.expires_at.is_(None),
                InstallSession.state != InstallSessionState.EXPIRED,
            )
            .values(expires_at=expires_at)
        )
        return result.rowcount

    @begin_session
    def delete_session(self, install_session_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(InstallSession).where(InstallSession.id == install_session_id))

    @begin_session
    def get_dashboard_sessions_for_user(
        self, user_id: int, session: Session = None  # type: ignore
    ) -> list[InstallSession]:
        """One row per game (its most recent session), active or DONE with a
        cache still on disk - backs the client's "active installers" view."""
        latest_per_game = (
            select(
                InstallSession.id,
                func.row_number()
                .over(
                    partition_by=InstallSession.game_id,
                    order_by=(InstallSession.created_at.desc(), InstallSession.id.desc()),
                )
                .label("rank"),
            )
            .where(InstallSession.user_id == user_id)
            .subquery()
        )
        latest_ids = select(latest_per_game.c.id).where(latest_per_game.c.rank == 1)
        return list(
            session.scalars(
                select(InstallSession)
                .where(
                    InstallSession.id.in_(latest_ids),
                    InstallSession.state.in_({*ACTIVE_INSTALL_STATES, InstallSessionState.DONE}),
                )
                .order_by(InstallSession.updated_at.desc())
            ).all()
        )

    @begin_session
    def get_sessions_for_game(
        self, game_id: int, session: Session = None  # type: ignore
    ) -> list[InstallSession]:
        return list(
            session.scalars(select(InstallSession).where(InstallSession.game_id == game_id)).all()
        )

    @begin_session
    def get_expired_sessions(self, session: Session = None) -> list[InstallSession]:  # type: ignore
        """Sessions whose TTL has elapsed. A still-running session is excluded
        even past its TTL - evicting its cache mid-write would corrupt it."""
        now = datetime.now(timezone.utc)
        return list(
            session.scalars(
                select(InstallSession).where(
                    InstallSession.expires_at.is_not(None),
                    InstallSession.expires_at < now,
                    InstallSession.state != InstallSessionState.EXPIRED,
                    InstallSession.state.not_in(RUNNING_INSTALL_STATES),
                )
            ).all()
        )
