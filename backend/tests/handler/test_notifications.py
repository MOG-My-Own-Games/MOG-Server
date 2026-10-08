from types import SimpleNamespace
from unittest.mock import patch

from handler import notifications
from models.library import Library
from models.notification import (
    KIND_AUTO_MODE_FAILED,
    KIND_AUTO_MODE_STUCK,
    KIND_GAMES_ADDED,
    KIND_MOD_FAILED,
    KIND_MOD_READY,
    KIND_SAVE_SYNCED,
)


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


def _users():
    return [
        SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[]),
        SimpleNamespace(id=2, is_admin=False, hidden_library_ids=[]),
        SimpleNamespace(id=3, is_admin=False, hidden_library_ids=[4]),  # may not see library 4
    ]


@patch("handler.notifications.db_user_handler")
@patch("handler.notifications.db_notification_handler")
class TestGamesAdded:
    def test_several_games_make_one_notification_per_user_who_can_see_the_library(self, store, users):
        users.get_all_users.return_value = _users()
        library = Library(id=4, name="Games", root_path="/library/games")

        notifications.notify_games_added(library, ((10, "Alpha"), (11, "Beta"), (12, "Gamma")))

        sent = [c.args[0] for c in store.add_notification.call_args_list]
        assert sorted(n.user_id for n in sent) == [1, 2]  # user 3 has the library hidden
        assert all(n.kind == KIND_GAMES_ADDED and n.title == "3 new games added" and n.game_id is None for n in sent)
        assert sent[0].body == "In Games: Alpha, Beta, Gamma."

    def test_a_single_game_names_itself_and_links_to_it(self, store, users):
        users.get_all_users.return_value = _users()[:1]

        notifications.notify_games_added(Library(id=4, name="Games", root_path="/g"), ((10, "Alpha"),))

        note = store.add_notification.call_args.args[0]
        assert note.title == "New game added: Alpha" and note.game_id == 10

    def test_a_long_list_is_cut_and_counted(self, store, users):
        users.get_all_users.return_value = _users()[:1]
        added = tuple((i, f"Game {i}") for i in range(14))

        notifications.notify_games_added(Library(id=4, name="L", root_path="/g"), added)

        body = store.add_notification.call_args.args[0].body
        assert "Game 9" in body and "Game 10" not in body and body.endswith("and 4 more.")

    def test_nothing_added_sends_nothing(self, store, users):
        notifications.notify_games_added(Library(id=4, name="L", root_path="/g"), ())
        store.add_notification.assert_not_called()


@patch("handler.notifications.db_notification_handler")
@patch("handler.notifications.db_game_handler")
def test_a_backed_up_save_notifies_its_user(games, store):
    games.get_game.return_value = SimpleNamespace(name="Some Game")

    notifications.notify_save_synced(3, 5, "karasu", "quit", 2)

    note = store.add_notification.call_args.args[0]
    assert (note.user_id, note.kind, note.game_id) == (3, KIND_SAVE_SYNCED, 5)
    assert note.title == "Saves backed up: Some Game" and note.body == "From karasu (quit), 2 files."


@patch("handler.notifications.db_notification_handler")
@patch("handler.notifications.db_game_handler")
def test_a_failing_notification_does_not_fail_the_upload(games, store):
    games.get_game.return_value = None
    store.add_notification.side_effect = RuntimeError("db down")
    notifications.notify_save_synced(3, 5, "karasu", "quit", 1)


@patch("handler.notifications.db_notification_handler")
@patch("handler.notifications.db_game_handler")
def test_a_zipped_mod_notifies_whoever_asked_for_it_and_a_failed_one_says_why(games, store):
    games.get_game.return_value = SimpleNamespace(name="Some Game")

    notifications.notify_mod_zipped(3, 5, "mod1")
    ready = store.add_notification.call_args.args[0]
    assert (ready.user_id, ready.kind, ready.game_id, ready.title) == (3, KIND_MOD_READY, 5, "Mod ready: mod1")

    notifications.notify_mod_zipped(3, 5, "mod1", "disk full")
    failed = store.add_notification.call_args.args[0]
    assert failed.kind == KIND_MOD_FAILED and "disk full" in failed.body


@patch("handler.notifications.db_notification_handler")
@patch("handler.notifications.db_game_handler")
def test_a_restored_save_notifies_its_user_and_says_where_it_came_from(games, store):
    from models.notification import KIND_SAVE_RESTORED

    games.get_game.return_value = SimpleNamespace(name="Some Game")

    notifications.notify_save_restored(3, 5, "deck", "karasu", 2)
    note = store.add_notification.call_args.args[0]
    assert (note.user_id, note.kind, note.game_id) == (3, KIND_SAVE_RESTORED, 5)
    assert note.title == "Saves restored: Some Game" and note.body == "On deck from karasu, 2 files."

    notifications.notify_save_restored(3, 5, "deck", None, 1)
    assert store.add_notification.call_args.args[0].body == "On deck, 1 file."

    store.add_notification.side_effect = RuntimeError("db down")
    notifications.notify_save_restored(3, 5, "deck", None, 1)  # never fails the request
