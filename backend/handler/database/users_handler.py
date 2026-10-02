from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.user import User

from .base_handler import DBBaseHandler


class DBUsersHandler(DBBaseHandler):
    @begin_session
    def add_user(self, user: User, session: Session = None) -> User:  # type: ignore
        session.add(user)
        session.flush()
        session.refresh(user)
        return user

    @begin_session
    def get_user(self, user_id: int, session: Session = None) -> User | None:  # type: ignore
        return session.get(User, user_id)

    @begin_session
    def get_user_by_username(self, username: str, session: Session = None) -> User | None:  # type: ignore
        return session.scalars(select(User).where(User.username == username)).first()

    @begin_session
    def get_all_users(self, session: Session = None) -> list[User]:  # type: ignore
        return list(session.scalars(select(User)).all())

    @begin_session
    def update_user(self, user_id: int, data: dict, session: Session = None) -> User | None:  # type: ignore
        session.execute(update(User).where(User.id == user_id).values(**data))
        return session.get(User, user_id)

    @begin_session
    def delete_user(self, user_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(User).where(User.id == user_id))
