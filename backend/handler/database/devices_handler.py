from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.base import utc_now
from models.device import Device

from .base_handler import DBBaseHandler


class DBDevicesHandler(DBBaseHandler):
    @begin_session
    def add_device(self, device: Device, session: Session = None) -> Device:  # type: ignore
        session.add(device)
        session.flush()
        session.refresh(device)
        return device

    @begin_session
    def get_device(self, id: int, session: Session = None) -> Device | None:  # type: ignore
        return session.get(Device, id)

    @begin_session
    def get_by_client_uid(
        self, user_id: int, client_uid: str, session: Session = None  # type: ignore
    ) -> Device | None:
        return session.scalar(select(Device).where(Device.user_id == user_id, Device.client_uid == client_uid))

    @begin_session
    def get_by_name(
        self, user_id: int, name: str, session: Session = None  # type: ignore
    ) -> Device | None:
        return session.scalar(select(Device).where(Device.user_id == user_id, Device.name == name))

    @begin_session
    def get_for_user(self, user_id: int, session: Session = None) -> list[Device]:  # type: ignore
        return list(session.scalars(select(Device).where(Device.user_id == user_id).order_by(Device.name)).all())

    @begin_session
    def update_device(self, id: int, data: dict, session: Session = None) -> Device:  # type: ignore
        session.execute(update(Device).where(Device.id == id).values(**data, updated_at=utc_now()))
        session.expire_all()
        return session.get(Device, id)

    @begin_session
    def delete_for_user(self, user_id: int, session: Session = None) -> int:  # type: ignore
        return session.execute(delete(Device).where(Device.user_id == user_id)).rowcount
