from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game_play_session import GamePlaySession

from .base_handler import DBBaseHandler


class DBPlaytimeHandler(DBBaseHandler):
    @begin_session
    def add(
        self,
        user_id: int,
        game_id: int,
        started_at: datetime,
        seconds: int,
        device_id: int | None = None,
        session: Session = None,  # type: ignore
    ) -> bool:
        """Keep one session. False when this user already has a session of the game that began at that moment (a client sending
        it again), which is left as it was."""
        found = session.scalar(
            select(GamePlaySession.id).where(
                GamePlaySession.user_id == user_id,
                GamePlaySession.game_id == game_id,
                GamePlaySession.started_at == started_at,
            )
        )
        if found is not None:
            return False
        session.add(
            GamePlaySession(user_id=user_id, game_id=game_id, device_id=device_id, started_at=started_at, seconds=seconds)
        )
        return True

    @begin_session
    def seconds_by_game(self, user_id: int, session: Session = None) -> dict[int, int]:  # type: ignore
        """What the user played of each game they played, in seconds."""
        rows = session.execute(
            select(GamePlaySession.game_id, func.sum(GamePlaySession.seconds))
            .where(GamePlaySession.user_id == user_id)
            .group_by(GamePlaySession.game_id)
        ).all()
        return {game_id: int(total) for game_id, total in rows}

    @begin_session
    def summary_for_game(self, user_id: int, game_id: int, session: Session = None) -> tuple[int, int]:  # type: ignore
        """(seconds played, sessions) of a game by a user."""
        total, count = session.execute(
            select(func.coalesce(func.sum(GamePlaySession.seconds), 0), func.count(GamePlaySession.id)).where(
                GamePlaySession.user_id == user_id, GamePlaySession.game_id == game_id
            )
        ).one()
        return int(total), int(count)
