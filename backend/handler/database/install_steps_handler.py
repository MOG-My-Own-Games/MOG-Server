from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.install_step import InstallStep

from .base_handler import DBBaseHandler


class DBInstallStepsHandler(DBBaseHandler):
    @begin_session
    def list_for_game(self, game_id: int, session: Session = None) -> list[InstallStep]:  # type: ignore
        """The installers of the game, first to last."""
        return list(
            session.scalars(
                select(InstallStep).where(InstallStep.game_id == game_id).order_by(InstallStep.position)
            ).all()
        )

    @begin_session
    def replace(self, game_id: int, steps: list[dict], session: Session = None) -> list[InstallStep]:  # type: ignore
        """Replace the whole order of a game. Each dict has `path` and an optional `source_path`; the position is
        the index in the list."""
        session.execute(delete(InstallStep).where(InstallStep.game_id == game_id))
        session.flush()
        for position, step in enumerate(steps):
            session.add(
                InstallStep(
                    game_id=game_id,
                    position=position,
                    path=step["path"],
                    source_path=step.get("source_path"),
                )
            )
        session.flush()
        return self.list_for_game(game_id, session=session)
