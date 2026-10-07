from handler.database import db_game_handler, db_library_handler
from models.game import Game
from models.library import Library


def _library(name):
    return db_library_handler.add_library(Library(name=name, root_path=f"/library/{name}"))


def test_the_revision_changes_when_a_game_is_added_changed_or_removed(db):
    library = _library("games")
    empty = db_game_handler.revision()

    game = db_game_handler.add_game(Game(library_id=library.id, fs_name="A", name="A"))
    added = db_game_handler.revision()
    assert added != empty

    assert db_game_handler.revision() == added  # nothing happened: the same value, so a client does not reload

    db_game_handler.update_game(game.id, {"name": "A (scraped)"})
    changed = db_game_handler.revision()
    assert changed != added

    db_game_handler.delete_game(game.id)
    assert db_game_handler.revision() == empty  # back to no games

    db_game_handler.add_game(Game(library_id=library.id, fs_name="B", name="B"))
    assert db_game_handler.revision() not in (empty, added, changed)  # one game gone and one new: still different


def test_games_in_a_hidden_library_do_not_move_the_users_revision(db):
    shown, hidden = _library("games"), _library("restricted")
    db_game_handler.add_game(Game(library_id=shown.id, fs_name="A", name="A"))
    before = db_game_handler.revision([hidden.id])

    db_game_handler.add_game(Game(library_id=hidden.id, fs_name="B", name="B"))

    assert db_game_handler.revision([hidden.id]) == before
    assert db_game_handler.revision() != before
