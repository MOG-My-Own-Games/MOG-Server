from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field

from handler.filesystem.fs_tags import parse_fs_tags


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
    missing_from_fs: bool = False
    # Gone from disk, but some user still has saves of it, so it is kept (not "missing").
    saves_only: bool = False
    media: dict[str, Any] | None = None
    # This user has a finished install whose cache is still on disk.
    installed: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fs_tags(self) -> list[str]:
        return parse_fs_tags(self.fs_name)


class GameFileSchema(BaseModel):
    path: str
    size_bytes: int
    category: str


class GameSizeSchema(BaseModel):
    size_bytes: int
    file_count: int


class GameFilesSchema(BaseModel):
    root_path: str
    files: list[GameFileSchema]


class MediaSelectionForm(BaseModel):
    """Artwork URLs by kind; a kind left out is untouched, null clears it."""

    cover: str | None = None
    banner: str | None = None
    hero: str | None = None
    logo: str | None = None
    icon: str | None = None


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
    missing: int
    total: int
    # Games without a match are being matched in the background.
    scraping: bool = False


class ScrapeResultSchema(BaseModel):
    library_id: int
    total: int
    scraped: int
