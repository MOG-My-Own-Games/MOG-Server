from types import SimpleNamespace
from unittest.mock import patch

from handler import notifications
from models.notification import KIND_AUTO_MODE_FAILED, KIND_AUTO_MODE_STUCK


def _session(auto_mode=True):
    return SimpleNamespace(id=7, user_id=3, game_id=5, auto_mode=auto_mode)


@patch("handler.notifications.db_notification_handler")
@patch("handler.notifications.db_game_handler")
@patch("handler.notifications.db_install_session_handler")
class TestAutoModeNotifications:
    def test_stuck_notifies_the_user_who_started_the_install(self, sessions, games, store):
        sessions.get_session.return_value = _session()
        games.get_game.return_value = SimpleNamespace(name="Some Game")

        notifications.notify_auto_mode_stuck(7, "No known button on screen")

        note = store.add_notification.call_args.args[0]
        assert (note.user_id, note.kind, note.game_id, note.session_id) == (3, KIND_AUTO_MODE_STUCK, 5, 7)
        assert "Some Game" in note.title and note.body == "No known button on screen"

    def test_failure_notifies_only_when_auto_mode_was_on(self, sessions, games, store):
        games.get_game.return_value = SimpleNamespace(name="Some Game")

        sessions.get_session.return_value = _session(auto_mode=False)
        notifications.notify_auto_mode_failed(7, "boom")
        store.add_notification.assert_not_called()

        sessions.get_session.return_value = _session(auto_mode=True)
        notifications.notify_auto_mode_failed(7, "boom")
        note = store.add_notification.call_args.args[0]
        assert (note.kind, note.body) == (KIND_AUTO_MODE_FAILED, "boom")

    def test_a_storage_error_does_not_escape_the_failure_path(self, sessions, games, store):
        sessions.get_session.return_value = _session()
        games.get_game.return_value = None
        store.add_notification.side_effect = RuntimeError("db down")

        notifications.notify_auto_mode_failed(7, "boom")


def test_auto_mode_is_on_by_default():
    from config import INSTALL_AUTO_MODE_DEFAULT

    assert INSTALL_AUTO_MODE_DEFAULT is True
