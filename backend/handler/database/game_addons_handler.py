from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game_addon import GameAddon

from .base_handler import DBBaseHandler


class DBGameAddonsHandler(DBBaseHandler):
    @begin_session
    def get(self, addon_id: int, session: Session = None) -> GameAddon | None:  # type: ignore
        return session.get(GameAddon, addon_id)

    @begin_session
    def list_for_game(self, game_id: int, session: Session = None) -> list[GameAddon]:  # type: ignore
        return list(
            session.scalars(
                select(GameAddon).where(GameAddon.game_id == game_id).order_by(GameAddon.category, GameAddon.id)
            ).all()
        )

    @begin_session
    def total_size(self, game_id: int, session: Session = None) -> int:  # type: ignore
        """The bytes the game's addons take on the server (their zips)."""
        return int(session.scalar(select(func.sum(GameAddon.size_bytes)).where(GameAddon.game_id == game_id)) or 0)

    @begin_session
    def list_all(self, session: Session = None) -> list[GameAddon]:  # type: ignore
        return list(session.scalars(select(GameAddon).order_by(GameAddon.game_id, GameAddon.category, GameAddon.id)).all())

    @begin_session
    def add(self, values: dict, session: Session = None) -> GameAddon:  # type: ignore
        row = GameAddon(**values)
        session.add(row)
        session.flush()
        session.refresh(row)
        return row

    @begin_session
    def rename(self, addon_id: int, name: str, session: Session = None) -> GameAddon | None:  # type: ignore
        row = session.get(GameAddon, addon_id)
        if row is None:
            return None
        row.name = name
        session.flush()
        session.refresh(row)
        return row

    @begin_session
    def delete(self, addon_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(GameAddon).where(GameAddon.id == addon_id))
