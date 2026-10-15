from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game_launch_settings import GameLaunchSettings
from utils.launch_args import patch_tags

from .base_handler import DBBaseHandler


class DBLaunchSettingsHandler(DBBaseHandler):
    @begin_session
    def get(
        self, game_id: int, device_id: int | None, session: Session = None  # type: ignore
    ) -> GameLaunchSettings | None:
        """The row of one device, or the one shared by all of them when `device_id` is None."""
        return session.scalar(
            select(GameLaunchSettings).where(
                GameLaunchSettings.game_id == game_id,
                GameLaunchSettings.device_id.is_(None) if device_id is None else GameLaunchSettings.device_id == device_id,
            )
        )

    @begin_session
    def list_for_game(self, game_id: int, session: Session = None) -> list[GameLaunchSettings]:  # type: ignore
        """Every row of the game: the shared one and each device's."""
        return list(session.scalars(select(GameLaunchSettings).where(GameLaunchSettings.game_id == game_id)).all())

    @begin_session
    def save(
        self,
        game_id: int,
        device_id: int | None,
        changes: dict,
        args_patch: tuple[list[str] | None, list[str] | None] | None = None,
        session: Session = None,  # type: ignore
    ) -> GameLaunchSettings:
        """Create the row or change the fields in `changes` (None clears one). `args_patch` is (tags to add, tags to remove) for
        the launch arguments, applied to the list the row holds now."""
        row = self.get(game_id, device_id, session=session)
        if row is None:
            row = GameLaunchSettings(game_id=game_id, device_id=device_id)
            session.add(row)
        for field, value in changes.items():
            setattr(row, field, value)
        if args_patch is not None and (args_patch[0] or args_patch[1] or row.launch_args is not None):
            add, remove = args_patch
            row.launch_args = patch_tags(row.launch_args, add, remove)  # a new list: the column is not told of a changed one
        session.flush()
        session.refresh(row)
        return row

    @begin_session
    def delete(self, game_id: int, device_id: int | None, session: Session = None) -> None:  # type: ignore
        row = self.get(game_id, device_id, session=session)
        if row is not None:
            session.delete(row)