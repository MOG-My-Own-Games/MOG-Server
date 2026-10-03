from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.notification import Notification

from .base_handler import DBBaseHandler


class DBNotificationsHandler(DBBaseHandler):
    @begin_session
    def add_notification(self, notification: Notification, session: Session = None) -> Notification:  # type: ignore
        session.add(notification)
        session.flush()
        session.refresh(notification)
        return notification

    @begin_session
    def get_for_user(self, user_id: int, limit: int = 100, session: Session = None) -> list[Notification]:  # type: ignore
        return list(
            session.scalars(
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.id.desc())
                .limit(limit)
            ).all()
        )

    @begin_session
    def count_unread(self, user_id: int, session: Session = None) -> int:  # type: ignore
        return session.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == user_id, Notification.read.is_(False)
            )
        ) or 0

    @begin_session
    def mark_read(self, user_id: int, notification_id: int | None = None, session: Session = None) -> None:  # type: ignore
        """One notification, or every unread one of the user's when no id is given."""
        query = update(Notification).where(Notification.user_id == user_id).values(read=True)
        if notification_id is not None:
            query = query.where(Notification.id == notification_id)
        session.execute(query)

    @begin_session
    def delete(self, user_id: int, notification_id: int | None = None, session: Session = None) -> int:  # type: ignore
        """One notification, or every one of the user's when no id is given."""
        query = delete(Notification).where(Notification.user_id == user_id)
        if notification_id is not None:
            query = query.where(Notification.id == notification_id)
        return session.execute(query).rowcount
