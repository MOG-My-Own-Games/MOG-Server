"""Creating notifications from the rest of the server."""

from __future__ import annotations

from handler.database import db_game_handler, db_install_session_handler, db_notification_handler, db_user_handler
from logger.logger import log
from models.library import Library
from models.notification import (
    BODY_MAX_LENGTH,
    KIND_AUTO_MODE_FAILED,
    KIND_AUTO_MODE_STUCK,
    KIND_GAMES_ADDED,
    KIND_SAVE_SYNCED,
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


NAMES_LISTED = 10  # games named in the body of a "games added" notification; the rest are counted


def notify_save_synced(user_id: int, game_id: int, device_name: str, trigger: str, file_count: int) -> None:
    """A device backed up a game's saves: the user can see on any of their machines that it went through."""
    try:
        notify(
            user_id,
            KIND_SAVE_SYNCED,
            f"Saves backed up: {_game_name(game_id)}",
            f"From {device_name} ({trigger}), {file_count} file{'' if file_count == 1 else 's'}.",
            game_id=game_id,
        )
    except Exception as e:  # noqa: BLE001 - a notification must never fail the upload
        log.warning(f"Could not create the save sync notification: {e}")


def notify_games_added(library: Library, added: tuple[tuple[int, str], ...]) -> None:
    """Games were added to a library: one notification per user who can see it, however many there are."""
    if not added:
        return
    if len(added) == 1:
        game_id, name = added[0]
        title, body = f"New game added: {name}", f"In {library.name}."
    else:
        game_id = None
        names = [name for _, name in added[:NAMES_LISTED]]
        more = len(added) - len(names)
        title = f"{len(added)} new games added"
        body = f"In {library.name}: " + ", ".join(names) + (f" and {more} more." if more else ".")
    for user in db_user_handler.get_all_users():
        if user.is_admin or library.id not in (user.hidden_library_ids or []):
            notify(user.id, KIND_GAMES_ADDED, title, body, game_id=game_id)
