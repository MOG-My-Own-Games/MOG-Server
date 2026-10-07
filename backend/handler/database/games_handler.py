from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game import Game
from models.save_version import SaveVersion

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
    def revision(self, hidden_library_ids: list[int] | None = None, session: Session = None) -> str:  # type: ignore
        """A short text that changes whenever a game is added, removed or changed (a scan, a scrape, an edit): how
        many games there are and when the last one was touched. Cheap, so a client can ask often."""
        query = select(func.count(Game.id), func.max(Game.updated_at))
        if hidden_library_ids:
            query = query.where(Game.library_id.not_in(hidden_library_ids))
        count, newest = session.execute(query).one()
        return f"{count}-{newest.isoformat() if newest else 0}"

    @begin_session
    def get_missing_games(self, library_id: int | None = None, session: Session = None) -> list[Game]:  # type: ignore
        query = select(Game).where(Game.missing_from_fs.is_(True)).order_by(Game.name)
        if library_id is not None:
            query = query.where(Game.library_id == library_id)
        return list(session.scalars(query).all())

    @begin_session
    def delete_missing_games(self, library_id: int | None = None, session: Session = None) -> int:  # type: ignore
        # A game with saves is kept: those are only reachable through it.
        query = delete(Game).where(
            Game.missing_from_fs.is_(True), Game.id.not_in(select(SaveVersion.game_id).distinct())
        )
        if library_id is not None:
            query = query.where(Game.library_id == library_id)
        return session.execute(query).rowcount

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
