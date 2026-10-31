"""A game a client imported for its saves, and the same game the scan found in a library: one game, not two.

The scan matches an import by the name of the folder, but a file such as `Name_v2_WindowsInstaller.exe` is only named like the game
once its metadata is applied. So this runs when a game's name changes and after each scan: a game with a folder that has the name of
an imported one takes its place."""

from __future__ import annotations

from handler import media_uploads
from handler.database import db_game_handler
from logger.logger import log
from models.game import Game


# The ids of the games merged away, by the id of the imported game that took their place (what a scan reported is told by it).
merged_into: dict[int, int] = {}


def import_key(name: str) -> str:
    from handler.scan_handler import import_key as key  # the scan's own idea of "the same name"

    return key(name)


def _uploaded_media(game: Game) -> bool:
    """Whether the game has artwork uploaded under its own id: that follows the id, so the game cannot give it up."""
    return f"{media_uploads.URL_PREFIX}{game.id}/" in str(game.media or "") + str(game.cover_path or "")


def adopt_placeholders(game_id: int | None = None, db=db_game_handler) -> Game | None:
    """Merge into its imported twin each game with a folder in a library (only `game_id`, when given). A game that anything hangs on
    (installs, saves, play time) is left alone and logged, since the two cannot be joined without losing something. Returns the game
    that is left of `game_id`, or None when it was not merged. `db` is the games handler the caller works with."""
    placeholders = {}
    for imported in db.get_imported_games():
        placeholders.setdefault(import_key(imported.name), imported)
    if not placeholders:
        return None
    games = [db.get_game(game_id)] if game_id is not None else db.get_all_games()
    result = None
    for game in games:
        if game is None or game.imported or game.library_id is None or game.missing_from_fs:
            continue
        twin = placeholders.get(import_key(game.name))
        if twin is None or twin.id == game.id:
            continue
        if db.has_dependents(game.id) or _uploaded_media(game):
            log.warning(f"{game.name} (#{game.id}) is the same game as #{twin.id}, which a client imported, but has installs or artwork of its own: not merged")
            continue
        merged = db.absorb_game(twin.id, game.id)
        placeholders.pop(import_key(twin.name), None)
        merged_into[game.id] = twin.id
        log.info(f"{game.name}: #{game.id} ({game.fs_name}) is the game #{twin.id} that a client imported: they are one game now")
        if game.id == game_id:
            result = merged
    return result
