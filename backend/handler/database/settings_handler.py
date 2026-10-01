from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.settings import SETTINGS_ROW_ID, Settings

from .base_handler import DBBaseHandler


class DBSettingsHandler(DBBaseHandler):
    @begin_session
    def get_settings(self, session: Session = None) -> Settings:  # type: ignore
        row = session.get(Settings, SETTINGS_ROW_ID)
        if row is None:
            row = Settings(id=SETTINGS_ROW_ID)
            session.add(row)
            session.flush()
            session.refresh(row)
        return row

    @begin_session
    def update_settings(self, data: dict, session: Session = None) -> Settings:  # type: ignore
        self.get_settings(session=session)  # ensure the row exists first
        session.execute(update(Settings).where(Settings.id == SETTINGS_ROW_ID).values(**data))
        return session.get(Settings, SETTINGS_ROW_ID)
