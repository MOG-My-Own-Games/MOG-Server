from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from starlette.concurrency import run_in_threadpool

from endpoints.responses.game import LibraryCreateForm, LibrarySchema, ScanResultSchema, ScrapeResultSchema
from handler.auth import AdminUser, CurrentUser
from handler.database import db_library_handler
from handler.scan_handler import scan_library
from handler.scrape_handler import scrape_library
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
async def scan_one_library(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> ScanResultSchema:
    library = db_library_handler.get_library(id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = await run_in_threadpool(scan_library, library)
    return ScanResultSchema(library_id=id, added=result.added, removed=result.removed, total=result.total)


@router.post("/{id}/scrape")
async def scrape_one_library(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> ScrapeResultSchema:
    """Best-effort metadata fill (IGDB + SteamGridDB) for every game in this
    library that's still missing it. See handler/scrape_handler.py for what
    "best-effort" means here."""
    library = db_library_handler.get_library(id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    result = await run_in_threadpool(scrape_library, id)
    return ScrapeResultSchema(library_id=id, total=result.total, scraped=result.scraped)
