"""Creating notifications from the rest of the server."""

from __future__ import annotations

from handler.database import db_game_handler, db_install_session_handler, db_notification_handler
from logger.logger import log
from models.notification import (
    BODY_MAX_LENGTH,
    KIND_AUTO_MODE_FAILED,
    KIND_AUTO_MODE_STUCK,
    TITLE_MAX_LENGTH,
    Notification,
)


def notify(
    user_id: int,
    kind: str,
    title: str,
    body: str | None = None,
    game_id: int | None = None,
    session_id: int | None = None,
) -> None:
    db_notification_handler.add_notification(
        Notification(
            user_id=user_id,
            kind=kind,
            title=title[:TITLE_MAX_LENGTH],
            body=body[:BODY_MAX_LENGTH] if body else None,
            game_id=game_id,
            session_id=session_id,
        )
    )


def _game_name(game_id: int) -> str:
    game = db_game_handler.get_game(game_id)
    return game.name if game else "a game"


def notify_auto_mode_stuck(install_session_id: int, detail: str | None) -> None:
    """Auto mode cannot find anything to press; the user has to finish by hand."""
    session = db_install_session_handler.get_session(install_session_id)
    if session is None:
        return
    notify(
        session.user_id,
        KIND_AUTO_MODE_STUCK,
        f"Auto mode needs your help: {_game_name(session.game_id)}",
        detail or "Auto mode could not find a button to press.",
        game_id=session.game_id,
        session_id=session.id,
    )


def notify_auto_mode_failed(install_session_id: int, error: str) -> None:
    """The install failed while auto mode was driving it."""
    session = db_install_session_handler.get_session(install_session_id)
    if session is None or not session.auto_mode:
        return
    try:
        notify(
            session.user_id,
            KIND_AUTO_MODE_FAILED,
            f"Auto mode install failed: {_game_name(session.game_id)}",
            error,
            game_id=session.game_id,
            session_id=session.id,
        )
    except Exception as e:  # noqa: BLE001 - a failed notification must not hide the install failure
        log.warning(f"Could not create the auto mode failure notification: {e}")
