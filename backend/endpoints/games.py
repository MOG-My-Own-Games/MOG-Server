from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from starlette.concurrency import run_in_threadpool

from endpoints.responses.game import GameSchema, GameUpdateForm
from handler.auth import AdminUser, CurrentUser
from handler.database import db_game_handler
from handler.metadata import igdb_handler, sgdb_handler
from handler.scrape_handler import scrape_game

router = APIRouter(prefix="/games", tags=["games"])


@router.get("")
async def list_games(user: CurrentUser, library_id: int | None = None) -> list[GameSchema]:
    games = (
        db_game_handler.get_games_for_library(library_id)
        if library_id is not None
        else db_game_handler.get_all_games()
    )
    return [GameSchema.model_validate(g) for g in games]


@router.get("/{id}")
async def get_game(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> GameSchema:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return GameSchema.model_validate(game)


@router.put("/{id}")
async def update_game(user: AdminUser, id: Annotated[int, Path(ge=1)], data: GameUpdateForm) -> GameSchema:
    """Manual metadata edit, for a field a scrape got wrong or when there's
    no provider match to scrape at all."""
    game = db_game_handler.update_game(id, data.model_dump(exclude_unset=True))
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return GameSchema.model_validate(game)


@router.post("/{id}/scrape")
async def scrape_one_game(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> GameSchema:
    """Best-effort metadata fill for this one game - see handler/scrape_handler.py."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await run_in_threadpool(scrape_game, game)
    return GameSchema.model_validate(db_game_handler.get_game(id))


@router.get("/{id}/metadata/igdb/search")
async def search_igdb(
    user: AdminUser, id: Annotated[int, Path(ge=1)], query: Annotated[str | None, Query()] = None
) -> list[dict]:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await run_in_threadpool(igdb_handler.search_games, query or game.name)


@router.post("/{id}/metadata/igdb/{igdb_id}")
async def apply_igdb_match(user: AdminUser, id: Annotated[int, Path(ge=1)], igdb_id: int) -> GameSchema:
    """Apply one IGDB search result. Re-fetches the full record by id first
    (search results can omit fields IGDB didn't populate in list context -
    see igdb_handler.get_game_by_id) so igdb_metadata always ends up with
    everything available, not just the trimmed shape search_games returns."""
    full = await run_in_threadpool(igdb_handler.get_game_by_id, igdb_id)
    if full is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not fetch this game from IGDB")
    game = db_game_handler.update_game(
        id, {"igdb_id": igdb_id, "name": full.get("name"), "summary": full.get("summary"), "igdb_metadata": full}
    )
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return GameSchema.model_validate(game)


@router.get("/{id}/metadata/sgdb/search")
async def search_sgdb(
    user: AdminUser, id: Annotated[int, Path(ge=1)], query: Annotated[str | None, Query()] = None
) -> list[str]:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await run_in_threadpool(sgdb_handler.search_grids, query or game.name)


@router.post("/{id}/metadata/sgdb")
async def apply_sgdb_cover(user: AdminUser, id: Annotated[int, Path(ge=1)], cover_path: str) -> GameSchema:
    game = db_game_handler.update_game(id, {"cover_path": cover_path})
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return GameSchema.model_validate(game)
