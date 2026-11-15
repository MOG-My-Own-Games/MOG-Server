from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from endpoints.responses.save import (
    BlacklistedSavesSchema,
    PurgeSavesSchema,
    SavePathsForm,
    SaveRulesAddForm,
    SaveRulesSchema,
)
from endpoints.saves import _visible_game, clean_paths
from handler import titles
from handler.auth import CurrentUser
from handler.database import db_save_rules_handler, db_saves_handler
from handler.saves import blacklisted_in_versions, purge_blacklisted, rules_for, run_on_saves_channel
from models.game import Game
from utils.archive_safety import ArchiveError

router = APIRouter(tags=["saves"])


def _schema(game: Game, user) -> SaveRulesSchema:
    blacklist, whitelist = rules_for(titles.version_ids(game, user))
    return SaveRulesSchema(blacklist=blacklist, whitelist=whitelist)


@router.get("/games/{id}/save-rules")
async def get_save_rules(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> SaveRulesSchema:
    """What the clients leave out of this game's saves and what they add to them. The same for every user and device, and for the
    versions of one title."""
    return _schema(_visible_game(user, id), user)


@router.post("/games/{id}/save-rules")
async def add_save_rules(user: CurrentUser, id: Annotated[int, Path(ge=1)], form: SaveRulesAddForm) -> SaveRulesSchema:
    """Put paths on the blacklist or the whitelist; a path that is on the other list moves to this one."""
    game = _visible_game(user, id)
    db_save_rules_handler.add(id, form.kind, clean_paths(form.paths))
    return _schema(game, user)


@router.post("/games/{id}/save-rules/remove")
async def remove_save_rules(user: CurrentUser, id: Annotated[int, Path(ge=1)], form: SavePathsForm) -> SaveRulesSchema:
    """Take paths off whichever list they are on (a blacklisted path is synced again the next time it is there)."""
    game = _visible_game(user, id)
    db_save_rules_handler.remove(titles.version_ids(game, user), clean_paths(form.paths))
    return _schema(game, user)


@router.get("/games/{id}/save-rules/blacklisted")
async def blacklisted_saves(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> BlacklistedSavesSchema:
    """The files the blacklist would leave in this user's stored versions of the game (all the versions of its title): what
    `purge` takes out."""
    game = _visible_game(user, id)
    ids = titles.version_ids(game, user)
    blacklist, whitelist = rules_for(ids)
    return BlacklistedSavesSchema(**blacklisted_in_versions(db_saves_handler.get_for_games(user.id, ids), blacklist, whitelist))


@router.post("/games/{id}/save-rules/purge")
async def purge_saves(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> PurgeSavesSchema:
    """Take every blacklisted file out of this user's stored versions of the game. A version with nothing else in it is
    deleted. The saves of other users are not touched."""
    game = _visible_game(user, id)
    ids = titles.version_ids(game, user)
    blacklist, whitelist = rules_for(ids)
    versions = db_saves_handler.get_for_games(user.id, ids)
    try:
        done = await run_on_saves_channel(purge_blacklisted, versions, blacklist, whitelist)
    except ArchiveError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    return PurgeSavesSchema(**done)
