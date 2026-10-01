from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game import Game

from .base_handler import DBBaseHandler


class DBGamesHandler(DBBaseHandler):
    @begin_session
    def add_game(self, game: Game, session: Session = None) -> Game:  # type: ignore
        session.add(game)
        session.flush()
        session.refresh(game)
        return game

    @begin_session
    def get_game(self, game_id: int, session: Session = None) -> Game | None:  # type: ignore
        return session.get(Game, game_id)

    @begin_session
    def get_game_by_fs_name(
        self, library_id: int, fs_name: str, session: Session = None  # type: ignore
    ) -> Game | None:
        return session.scalars(
            select(Game).where(Game.library_id == library_id, Game.fs_name == fs_name)
        ).first()

    @begin_session
    def get_games_for_library(self, library_id: int, session: Session = None) -> list[Game]:  # type: ignore
        return list(
            session.scalars(
                select(Game).where(Game.library_id == library_id).order_by(Game.name)
            ).all()
        )

    @begin_session
    def get_all_games(self, session: Session = None) -> list[Game]:  # type: ignore
        return list(session.scalars(select(Game).order_by(Game.name)).all())

    @begin_session
    def update_game(self, game_id: int, data: dict, session: Session = None) -> Game | None:  # type: ignore
        session.execute(update(Game).where(Game.id == game_id).values(**data))
        return session.get(Game, game_id)

    @begin_session
    def delete_game(self, game_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(Game).where(Game.id == game_id))
