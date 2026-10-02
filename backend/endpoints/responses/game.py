from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class GameSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    library_id: int
    fs_name: str
    name: str
    summary: str | None
    igdb_id: int | None
    igdb_metadata: dict[str, Any] | None
    sgdb_id: int | None
    cover_path: str | None
    # This user has a finished install whose cache is still on disk.
    installed: bool = False


class GameUpdateForm(BaseModel):
    """Manual metadata edit - every field optional, only what's sent is
    changed (see the endpoint's exclude_unset). igdb_id/sgdb_id are here for
    when a scrape couldn't find (or picked the wrong) match - setting them
    directly only records the id/cover; it doesn't re-fetch IGDB's full
    metadata the way the search-and-apply flow does (see games.py's
    apply_igdb_match) - search-and-apply is the better path when IGDB is
    reachable at all."""

    name: str | None = None
    summary: str | None = None
    cover_path: str | None = None
    igdb_id: int | None = None
    sgdb_id: int | None = None


class LibrarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    root_path: str


class LibraryCreateForm(BaseModel):
    name: str
    root_path: str


class ScanResultSchema(BaseModel):
    library_id: int
    added: int
    removed: int
    total: int


class ScrapeResultSchema(BaseModel):
    library_id: int
    total: int
    scraped: int
