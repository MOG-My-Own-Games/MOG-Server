"""Merge two games by hand (Edit metadata > Merge...).

Two cases, told by whether each game has a folder of its own in a library:

- **merge**: at most one has a folder (a game a client imported, one that went from disk, the same game found twice). The game
  the merge is started from stays and the other goes, with everything it had moved over: saves, time played, launch settings,
  installs, add-ons. Nothing is lost that the person did not choose to drop (the time played has the choice).
- **link**: both have a folder, so they are two versions of one game and nothing is deleted. They are tied to one title
  (handler/titles.py), which is what makes them share the time played, the saves and the name and artwork.

Nothing is touched until `merge`/`link` is called; `preview` only reads."""

from __future__ import annotations

import shutil
from pathlib import Path

from config import ADDONS_PATH
from handler import media_uploads, titles
from handler import media as media_handler
from handler.database import db_game_handler, db_saves_handler
from handler.saves import resolve_path
from logger.logger import log
from models.game import Game

# The ids of the games merged away, by the id of the game that took their place (see handler/imports.py).
from handler.imports import merged_into

FILE_COLUMNS = ("library_id", "fs_name", "size_bytes", "addons_only", "has_mods")


class MergeRefused(ValueError):
    """The merge cannot be done now; `code` tells why (the endpoint answers 409 with it)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def has_files(game: Game) -> bool:
    """Whether the game has a folder of its own in a library (an imported game or one gone from disk has not)."""
    return game.library_id is not None and not game.imported and not game.missing_from_fs


def mode(this: Game, other: Game) -> str:
    return "link" if has_files(this) and has_files(other) else "merge"


def conflict(this: Game, other: Game, db=db_game_handler) -> str | None:
    if db.has_active_install(this.id) or db.has_active_install(other.id):
        return "install_in_progress"
    return None


def counts(this: Game, other: Game, db=db_game_handler) -> dict[str, dict[str, int]]:
    return {"this": db.counts(this.id), "other": db.counts(other.id)}


# --- the files that go with a game, moved before the database is changed (and put back if that fails) ---


class _Moves:
    """Files moved or copied on the way; `undo` puts everything back, `commit` removes what the merge no longer needs."""

    def __init__(self) -> None:
        self.moved: list[tuple[Path, Path]] = []
        self.copied: list[Path] = []

    def move(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        self.moved.append((source, target))

    def copy(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.copied.append(target)

    def undo(self) -> None:
        for source, target in reversed(self.moved):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            except OSError as e:
                log.error(f"Merge: could not put {target} back to {source}: {e}")
        for target in self.copied:
            target.unlink(missing_ok=True)


def _move_saves(keep: Game, drop: Game, moves: _Moves) -> dict[int, str]:
    """The saves of `drop` go under `keep`'s id: the new path of each version."""
    paths: dict[int, str] = {}
    for version in db_saves_handler.get_for_game_of_all_users(drop.id):
        parts = Path(version.file_path).parts  # <user>/<game>/<device>/<stamp>.zip
        if len(parts) != 4 or parts[1] != str(drop.id):
            continue
        relative = "/".join((parts[0], str(keep.id), parts[2], parts[3]))
        target = resolve_path(relative)
        if target.exists():
            relative = f"{relative[:-4]}-{version.id}.zip"
            target = resolve_path(relative)
        source = resolve_path(version.file_path)
        if source.is_file():
            moves.move(source, target)
        paths[version.id] = relative
    return paths


def _move_addons(keep: Game, drop: Game, moves: _Moves) -> None:
    source_root = Path(ADDONS_PATH) / str(drop.id)
    if not source_root.is_dir():
        return
    for category in source_root.iterdir():
        for entity in category.iterdir():
            moves.move(entity, Path(ADDONS_PATH) / str(keep.id) / category.name / entity.name)


# --- the columns the kept game ends with ---


def _artwork(keep: Game, drop: Game, choice: dict, moves: _Moves) -> tuple[dict, str | None]:
    """The artwork of each kind after the merge: the kept game's, the other's (an upload is copied under the kept id) or none."""
    media = dict(keep.media or {})
    for kind in media_handler.KINDS:
        picked = choice.get(kind, "this")
        if picked == "this":
            continue
        entry = (drop.media or {}).get(kind) if picked == "other" else None
        if entry is None:
            media.pop(kind, None)
            continue
        if media_uploads.is_upload(entry.get("url")):
            source = media_uploads.path_for(entry["url"])
            if source is None or not source.is_file():
                media.pop(kind, None)
                continue
            target = media_uploads.media_dir() / str(keep.id) / source.name
            moves.copy(source, target)
            entry = {"url": f"{media_uploads.URL_PREFIX}{keep.id}/{source.name}", "source": media_uploads.UPLOAD}
        media[kind] = entry
    cover = (media.get("cover") or {}).get("url")
    return media, cover


def _values(keep: Game, drop: Game, form: dict, moves: _Moves) -> dict:
    values: dict = {}
    for field in ("name", "summary"):
        if field in form:
            values[field] = form[field]
    if form.get("igdb_id", "this") == "other":
        values.update(igdb_id=drop.igdb_id, igdb_metadata=drop.igdb_metadata)
    if form.get("sgdb_id", "this") == "other":
        values["sgdb_id"] = drop.sgdb_id
    for field in ("hltb_id", "hltb_metadata"):  # what the kept game lacks, the other one lends
        if not getattr(keep, field) and getattr(drop, field):
            values[field] = getattr(drop, field)
    values["media"], values["cover_path"] = _artwork(keep, drop, form.get("media") or {}, moves)
    if has_files(drop) and not has_files(keep):
        values.update({column: getattr(drop, column) for column in FILE_COLUMNS}, missing_from_fs=False)
    values["imported"] = keep.imported and drop.imported
    values["title_key"] = None
    return values


def merge(keep: Game, drop: Game, form: dict, db=db_game_handler) -> Game:
    """Merge `drop` into `keep` (mode "merge"). `form` has the person's choices: `name`, `summary` (the values),
    `igdb_id`, `sgdb_id` ("this" or "other"), `media` (kind -> "this", "other" or "none") and `playtime`."""
    if mode(keep, drop) != "merge":
        raise MergeRefused("both_have_files", "Both games have their own files: they are versions of one game")
    if (reason := conflict(keep, drop, db)) is not None:
        raise MergeRefused(reason, "An install of one of the games is in progress")
    moves = _Moves()
    choice = form.get("media") or {}
    replaced = [
        entry["url"]
        for kind, entry in (keep.media or {}).items()
        if choice.get(kind, "this") != "this" and media_uploads.is_upload(entry.get("url"))
    ]
    users = {v.user_id for v in db_saves_handler.get_for_game_of_all_users(drop.id)}
    try:
        save_paths = _move_saves(keep, drop, moves)
        _move_addons(keep, drop, moves)
        values = _values(keep, drop, form, moves)
        merged = db.merge_games(
            keep.id, drop.id, values, form.get("playtime", "sum"), save_paths,
            steps_from_drop=has_files(drop) and not has_files(keep),
        )
    except BaseException:
        moves.undo()
        raise
    _clean_up(keep, drop, replaced, users)
    merged_into[drop.id] = keep.id
    log.info(f"Merged game #{drop.id} ({drop.name}) into #{keep.id} ({keep.name}) by hand")
    return merged


def _clean_up(keep: Game, drop: Game, replaced: list[str], users: set[int]) -> None:
    """What the merge left behind: the other game's artwork, add-on and (empty) save folders, and the kept game's uploads that
    the other game's artwork replaced."""
    media_uploads.purge(drop.id)
    for url in replaced:
        media_uploads.remove(url)
    shutil.rmtree(Path(ADDONS_PATH) / str(drop.id), ignore_errors=True)
    for user_id in users:
        shutil.rmtree(resolve_path(f"{user_id}/{drop.id}"), ignore_errors=True)


def link(this: Game, other: Game, form: dict, user, db=db_game_handler) -> Game:
    """Tie two versions to one title (mode "link"): the IGDB id the person chose goes to both, or, when neither game has one,
    a title of our own. The name, summary and artwork they chose go to both."""
    if mode(this, other) != "link":
        raise MergeRefused("not_versions", "One of the games has no files: merge them instead")
    chosen = other if form.get("igdb_id") == "other" else this
    shared: dict = {}
    for field in ("name", "summary"):
        if field in form:
            shared[field] = form[field]
    if chosen.igdb_id:
        for game in (this, other):
            db.update_game(game.id, {**shared, "igdb_id": chosen.igdb_id, "title_key": None})
    else:
        for game in (this, other):
            db.update_game(game.id, {**shared, "title_key": f"mog:{this.id}"})
    if form.get("sgdb_id") == "other":
        db.update_game(this.id, {"sgdb_id": other.sgdb_id})
    for kind, picked in (form.get("media") or {}).items():
        if picked == "this":
            entry = (this.media or {}).get(kind)
        elif picked == "other":
            entry = (other.media or {}).get(kind)
        else:
            entry = None
        for game in (this, other):
            titles.share_artwork(db.get_game(game.id), kind, entry, db)
    return db.get_game(this.id)
