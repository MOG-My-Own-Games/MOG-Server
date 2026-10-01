from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.library import Library

from .base_handler import DBBaseHandler


class DBLibrariesHandler(DBBaseHandler):
    @begin_session
    def add_library(self, library: Library, session: Session = None) -> Library:  # type: ignore
        session.add(library)
        session.flush()
        session.refresh(library)
        return library

    @begin_session
    def get_library(self, library_id: int, session: Session = None) -> Library | None:  # type: ignore
        return session.get(Library, library_id)

    @begin_session
    def get_all_libraries(self, session: Session = None) -> list[Library]:  # type: ignore
        return list(session.scalars(select(Library).order_by(Library.name)).all())

    @begin_session
    def delete_library(self, library_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(Library).where(Library.id == library_id))
