from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, status
from starlette.concurrency import run_in_threadpool

from endpoints.responses.game import LibraryCreateForm, LibrarySchema, ScanResultSchema, ScrapeResultSchema
from handler.auth import AdminUser, CurrentUser
from handler.database import db_game_handler, db_library_handler
from handler.scan_handler import scan_library
from handler.scrape_handler import needs_scrape, scrape_library, scrape_library_in_background
from models.library import Library

router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("")
async def list_libraries(user: CurrentUser) -> list[LibrarySchema]:
    libraries = db_library_handler.get_all_libraries()
    if not user.is_admin and user.hidden_library_ids:
        libraries = [lib for lib in libraries if lib.id not in user.hidden_library_ids]
    return [LibrarySchema.model_validate(lib) for lib in libraries]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_library(user: AdminUser, data: LibraryCreateForm) -> LibrarySchema:
    library = db_library_handler.add_library(Library(name=data.name, root_path=data.root_path))
    return LibrarySchema.model_validate(library)


@router.delete("/{id}")
async def delete_library(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> None:
    db_library_handler.delete_library(id)


@router.post("/{id}/scan")
async def scan_one_library(
    user: AdminUser, id: Annotated[int, Path(ge=1)], background: BackgroundTasks
) -> ScanResultSchema:
    library = db_library_handler.get_library(id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = await run_in_threadpool(scan_library, library)
    scraping = any(needs_scrape(g) for g in db_game_handler.get_games_for_library(id))
    if scraping:
        background.add_task(scrape_library_in_background, id)
    return ScanResultSchema(
        library_id=id, added=result.added, missing=result.missing, total=result.total, scraping=scraping
    )


@router.post("/{id}/scrape")
async def scrape_one_library(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> ScrapeResultSchema:
    """Re-fetch metadata and covers (IGDB + SteamGridDB) for every present game
    in this library, replacing what is stored. The automatic pass after a scan
    only fills what is missing."""
    library = db_library_handler.get_library(id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = await run_in_threadpool(scrape_library, id, True)
    return ScrapeResultSchema(library_id=id, total=result.total, scraped=result.scraped)
