from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.save_version import SaveVersion

from .base_handler import DBBaseHandler


class DBSavesHandler(DBBaseHandler):
    @begin_session
    def add_version(self, version: SaveVersion, session: Session = None) -> SaveVersion:  # type: ignore
        session.add(version)
        session.flush()
        session.refresh(version)
        return version

    @begin_session
    def get_version(self, id: int, session: Session = None) -> SaveVersion | None:  # type: ignore
        return session.get(SaveVersion, id)

    @begin_session
    def get_for_game(self, user_id: int, game_id: int, session: Session = None) -> list[SaveVersion]:  # type: ignore
        """Every device's versions of one game for one user, newest first."""
        return list(
            session.scalars(
                select(SaveVersion)
                .where(SaveVersion.user_id == user_id, SaveVersion.game_id == game_id)
                .order_by(SaveVersion.created_at.desc(), SaveVersion.id.desc())
            ).all()
        )

    @begin_session
    def get_latest(
        self, user_id: int, game_id: int, device_id: int, session: Session = None  # type: ignore
    ) -> SaveVersion | None:
        return session.scalar(
            select(SaveVersion)
            .where(
                SaveVersion.user_id == user_id,
                SaveVersion.game_id == game_id,
                SaveVersion.device_id == device_id,
            )
            .order_by(SaveVersion.created_at.desc(), SaveVersion.id.desc())
            .limit(1)
        )

    @begin_session
    def delete_beyond_newest(
        self, user_id: int, game_id: int, device_id: int, keep: int, session: Session = None  # type: ignore
    ) -> list[SaveVersion]:
        """Delete all but the `keep` newest versions of one device's game; return the deleted rows."""
        stale = list(
            session.scalars(
                select(SaveVersion)
                .where(
                    SaveVersion.user_id == user_id,
                    SaveVersion.game_id == game_id,
                    SaveVersion.device_id == device_id,
                )
                .order_by(SaveVersion.created_at.desc(), SaveVersion.id.desc())
                .offset(keep)
            ).all()
        )
        for version in stale:
            session.delete(version)
        return stale

    @begin_session
    def delete_version(self, id: int, session: Session = None) -> None:  # type: ignore
        version = session.get(SaveVersion, id)
        if version is not None:
            session.delete(version)

    @begin_session
    def game_ids_with_saves(self, session: Session = None) -> set[int]:  # type: ignore
        """Games any user has a saved version of."""
        return set(session.scalars(select(SaveVersion.game_id).distinct()).all())

    @begin_session
    def summary(
        self, user_id: int | None = None, game_id: int | None = None, session: Session = None  # type: ignore
    ) -> tuple[int, int]:
        """(versions, bytes) stored, for one user, one game (all users) or both."""
        query = select(func.count(SaveVersion.id), func.coalesce(func.sum(SaveVersion.size_bytes), 0))
        if user_id is not None:
            query = query.where(SaveVersion.user_id == user_id)
        if game_id is not None:
            query = query.where(SaveVersion.game_id == game_id)
        count, size = session.execute(query).one()
        return int(count), int(size)

    @begin_session
    def delete_for_game(self, game_id: int, session: Session = None) -> list[int]:  # type: ignore
        """Delete every user's versions of a game; returns the ids of the users who had any."""
        users = list(session.scalars(select(SaveVersion.user_id).where(SaveVersion.game_id == game_id).distinct()))
        session.execute(delete(SaveVersion).where(SaveVersion.game_id == game_id))
        return users

    @begin_session
    def delete_for_user(self, user_id: int, session: Session = None) -> int:  # type: ignore
        return session.execute(delete(SaveVersion).where(SaveVersion.user_id == user_id)).rowcount
