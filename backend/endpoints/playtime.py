from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from endpoints.responses.playtime import PlaytimeForm, PlaytimeSchema
from endpoints.saves import _own_device, _visible_game
from handler.auth import CurrentUser
from handler.database import db_playtime_handler
from logger.logger import log
from models.base import utc_now

router = APIRouter(tags=["playtime"])

# A session that began after this is from a clock that is wrong, and would count for time that has not come.
FUTURE_TOLERANCE = timedelta(days=1)


@router.get("/games/{id}/playtime")
async def get_playtime(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> PlaytimeSchema:
    _visible_game(user, id)
    seconds, sessions = db_playtime_handler.summary_for_game(user.id, id)
    return PlaytimeSchema(game_id=id, playtime_seconds=seconds, sessions=sessions)


@router.post("/games/{id}/playtime")
async def add_playtime(user: CurrentUser, id: Annotated[int, Path(ge=1)], form: PlaytimeForm) -> PlaytimeSchema:
    """Add one session of the user's to the time they have played the game. Told by when it started, so sending the same
    one again (a client that did not get the answer) changes nothing."""
    _visible_game(user, id)
    if form.device_id is not None:
        _own_device(user, form.device_id)
    if form.started_at > utc_now() + FUTURE_TOLERANCE:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The session starts in the future")
    started_at = form.started_at.replace(microsecond=0)
    recorded = db_playtime_handler.add(user.id, id, started_at, form.seconds, form.device_id)
    seconds, sessions = db_playtime_handler.summary_for_game(user.id, id)
    log.debug(f"Playtime: game {id}, user {user.id}: {form.seconds}s {'added' if recorded else 'was known'}, total {seconds}s")
    return PlaytimeSchema(game_id=id, playtime_seconds=seconds, sessions=sessions, recorded=recorded)
