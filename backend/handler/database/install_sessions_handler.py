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
        """Sessions actually holding a place right now - narrower than every
        ACTIVE state, used to bound INSTALL_MAX_CONCURRENCY. One that runs no
        installer (the files are taken as they are) never holds a place."""
        return (
            session.scalar(
                select(func.count(InstallSession.id)).where(
                    InstallSession.state.in_(RUNNING_INSTALL_STATES),
                    InstallSession.extract_only.is_(False),
                )
            )
            or 0
        )

    @staticmethod
    def _queue_order():
        return (
            InstallSession.queue_rank.is_(None),
            InstallSession.queue_rank.asc(),
            InstallSession.updated_at.asc(),
            InstallSession.id.asc(),
        )

    @begin_session
    def get_queued_ids(self, session: Session = None) -> list[int]:  # type: ignore
        """The waiting sessions, the next to start first."""
        return list(
            session.scalars(
                select(InstallSession.id)
                .where(InstallSession.state == InstallSessionState.QUEUED)
                .order_by(*self._queue_order())
            ).all()
        )

    @begin_session
    def next_queue_rank(self, session: Session = None) -> int:  # type: ignore
        """The rank that puts a session at the end of the line."""
        return (
            session.scalar(
                select(func.max(InstallSession.queue_rank)).where(InstallSession.state == InstallSessionState.QUEUED)
            )
            or 0
        ) + 1

    @begin_session
    def reorder_queue(self, wanted_ids: list[int], session: Session = None) -> list[int]:  # type: ignore
        """Put the waiting sessions in `wanted_ids` in that order, in the places they hold now; the others keep theirs.
        Ids that are not waiting are ignored. Returns the whole line afterwards."""
        line = list(
            session.scalars(
                select(InstallSession.id)
                .where(InstallSession.state == InstallSessionState.QUEUED)
                .order_by(*self._queue_order())
            ).all()
        )
        chosen = [i for i in dict.fromkeys(wanted_ids) if i in line]
        places = iter(chosen)
        line = [next(places) if i in chosen else i for i in line]
        for rank, session_id in enumerate(line, start=1):
            session.execute(update(InstallSession).where(InstallSession.id == session_id).values(queue_rank=rank))
        return line

    @begin_session
    def get_next_queued(self, session: Session = None) -> InstallSession | None:  # type: ignore
        """The session at the head of the line."""
        return session.scalars(
            select(InstallSession)
            .where(InstallSession.state == InstallSessionState.QUEUED)
            .order_by(*self._queue_order())
        ).first()

    @begin_session
    def queue_position(self, install_session_id: int, session: Session = None) -> int | None:  # type: ignore
        """Where a queued session stands, 1 being the next to start; None when it is not queued."""
        queued = self.get_queued_ids(session=session)
        return queued.index(install_session_id) + 1 if install_session_id in queued else None

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
                    InstallSession.state != InstallSessionState.QUEUED,  # nothing of it is on disk yet
                    InstallSession.state.not_in(RUNNING_INSTALL_STATES),
                )
            ).all()
        )
