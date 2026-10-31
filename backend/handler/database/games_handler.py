from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from decorators.database import begin_session
from models.game import Game
from models.game_addon import GameAddon
from models.game_launch_settings import GameLaunchSettings
from models.game_play_session import GamePlaySession
from models.install_session import InstallSession
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
            # NULL NOT IN (...) is NULL, so an imported game (no library) needs saying.
            query = query.where(or_(Game.library_id.is_(None), Game.library_id.not_in(hidden_library_ids)))
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
    def get_imported_games(self, session: Session = None) -> list[Game]:  # type: ignore
        return list(session.scalars(select(Game).where(Game.imported.is_(True)).order_by(Game.id)).all())

    @begin_session
    def get_all_games(self, session: Session = None) -> list[Game]:  # type: ignore
        return list(session.scalars(select(Game).order_by(Game.name)).all())

    @begin_session
    def update_game(self, game_id: int, data: dict, session: Session = None) -> Game | None:  # type: ignore
        session.execute(update(Game).where(Game.id == game_id).values(**data))
        return session.get(Game, game_id)

    @begin_session
    def has_dependents(self, game_id: int, session: Session = None) -> bool:  # type: ignore
        """Whether anything but the game's own row hangs on it: installs, add-ons, launch settings, play time, saves."""
        return any(
            session.scalar(select(func.count()).select_from(model).where(model.game_id == game_id))
            for model in (InstallSession, GameAddon, GameLaunchSettings, GamePlaySession, SaveVersion)
        )

    @begin_session
    def absorb_game(self, placeholder_id: int, game_id: int, session: Session = None) -> Game:  # type: ignore
        """The game a client imported takes the place of the game the scan found for it: it gets its folder, size and whatever
        metadata it lacks, and the other row goes. The imported one is kept, so the saves and the clients' records stay on its id."""
        placeholder, real = session.get(Game, placeholder_id), session.get(Game, game_id)
        taken = {f: getattr(real, f) for f in ("library_id", "fs_name", "size_bytes", "addons_only", "has_mods")}
        for field in ("summary", "igdb_id", "igdb_metadata", "sgdb_id", "hltb_id", "hltb_metadata", "cover_path", "media"):
            if not getattr(placeholder, field):
                taken[field] = getattr(real, field)
        session.execute(delete(Game).where(Game.id == game_id))
        session.flush()  # the folder's name is free again before the other row has it
        session.execute(update(Game).where(Game.id == placeholder_id).values(**taken, imported=False, missing_from_fs=False))
        session.expire_all()
        return session.get(Game, placeholder_id)

    @begin_session
    def delete_game(self, game_id: int, session: Session = None) -> None:  # type: ignore
        session.execute(delete(Game).where(Game.id == game_id))
