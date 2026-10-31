"""A title is one game with one or more versions (a version is a row of `games`: a folder, an installer).

The versions of a title share what a person means by "the game": the time played, the saves a client can offer, the name and
artwork. Nothing is moved to make that so: each row keeps what was written under its own id and the title is worked out when
something is read, so fixing a wrong IGDB match (or splitting a version off) undoes it.

The title of a game is told by, in this order: `title_key` (set by hand or by Merge, `mog:<id>`), the IGDB id (`igdb:<id>`), or
its own id (`mog:<id>`, a game by itself)."""

from __future__ import annotations

from handler import media_uploads
from handler.database import db_game_handler
from models.game import Game


def title_key(game: Game) -> str:
    return game.effective_title_key


def seen_by(user, game: Game) -> bool:
    return user.is_admin or game.library_id not in user.hidden_library_ids


def versions(game: Game, user, db=db_game_handler) -> list[Game]:
    """The games of the title `game` belongs to that `user` may see, `game` first and then the others by id."""
    key = title_key(game)
    others = [g for g in db.get_all_games() if g.id != game.id and title_key(g) == key and seen_by(user, g)]
    return [game, *sorted(others, key=lambda g: g.id)]


def version_ids(game: Game, user, db=db_game_handler) -> list[int]:
    return [g.id for g in versions(game, user, db)]


def groups(games: list[Game]) -> dict[int, list[int]]:
    """For each game of the list (already limited to what the user sees), the ids of all versions of its title in the list."""
    by_key: dict[str, list[int]] = {}
    for game in games:
        by_key.setdefault(title_key(game), []).append(game.id)
    return {game.id: by_key[title_key(game)] for game in games}


def share_artwork(game: Game, kind: str, entry: dict | None, db=db_game_handler) -> None:
    """Give `game` (another version of the title) the artwork just chosen for `kind` (`entry` is `{url, source}`, None to
    clear it). An upload is copied under the game's own id, since an upload belongs to the game it was made for."""
    media = dict(game.media or {})
    previous = (media.get(kind) or {}).get("url")
    if previous and media_uploads.is_upload(previous):
        media_uploads.remove(previous)
    update: dict = {}
    if entry is None:
        media.pop(kind, None)
        if kind == "cover":
            update["cover_path"] = None
    else:
        if media_uploads.is_upload(entry["url"]):
            source = media_uploads.path_for(entry["url"])
            if source is None or not source.is_file():
                return
            entry = {"url": media_uploads.store(game.id, kind, source.read_bytes()), "source": media_uploads.UPLOAD}
        media[kind] = entry
        if kind == "cover":
            update["cover_path"] = entry["url"]
    db.update_game(game.id, {**update, "media": media})
