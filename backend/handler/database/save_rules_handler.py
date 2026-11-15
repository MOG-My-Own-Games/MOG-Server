from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game_save_rule import GameSaveRule

from .base_handler import DBBaseHandler


class DBSaveRulesHandler(DBBaseHandler):
    @begin_session
    def get_for_games(self, game_ids: list[int], session: Session = None) -> list[GameSaveRule]:  # type: ignore
        """The rules of every version of a title, oldest first."""
        return list(
            session.scalars(
                select(GameSaveRule).where(GameSaveRule.game_id.in_(game_ids)).order_by(GameSaveRule.id)
            ).all()
        )

    @begin_session
    def add(self, game_id: int, kind: str, paths: list[str], session: Session = None) -> None:  # type: ignore
        """Put each path under `kind` for the game; a path that has the other kind already changes to this one."""
        have = {r.path: r for r in session.scalars(select(GameSaveRule).where(GameSaveRule.game_id == game_id))}
        for path in dict.fromkeys(paths):
            if path in have:
                have[path].kind = kind
            else:
                session.add(GameSaveRule(game_id=game_id, path=path, kind=kind))

    @begin_session
    def remove(self, game_ids: list[int], paths: list[str], session: Session = None) -> int:  # type: ignore
        """Forget the paths for these games (all the versions of a title); returns how many rows went."""
        return session.execute(
            delete(GameSaveRule).where(GameSaveRule.game_id.in_(game_ids), GameSaveRule.path.in_(paths))
        ).rowcount
