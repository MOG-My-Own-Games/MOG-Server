"""The installers of a game, in the order they run, kept per game (not per user) so the order survives cache purges.
Position 0 is the game installer; every later one is a patch (see handler/install/runner.py)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import BaseModel

INSTALL_STEP_PATH_MAX_LENGTH = 1000
# A game with more chained installers than this is not a game anyone has; it keeps the list bounded.
INSTALL_STEPS_MAX = 20


class InstallStep(BaseModel):
    __tablename__ = "install_steps"
    __table_args__ = (Index("ix_install_steps_game_position", "game_id", "position", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    # Relative to the game's directory, like InstallSession.installer_path.
    path: Mapped[str] = mapped_column(String(length=INSTALL_STEP_PATH_MAX_LENGTH))
    # The archive or disc image the installer is extracted from, when it is not run from the game's files.
    source_path: Mapped[str | None] = mapped_column(String(length=INSTALL_STEP_PATH_MAX_LENGTH), default=None)
