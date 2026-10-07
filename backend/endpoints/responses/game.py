from __future__ import annotations

from datetime import datetime
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
    # Completion times in seconds; empty when the game is not scraped or HowLongToBeat had nothing.
    hltb_id: int | None = None
    hltb_metadata: dict[str, Any] | None = None
    cover_path: str | None
    missing_from_fs: bool = False
    # Gone from disk, but some user still has saves of it, so it is kept (not "missing").
    saves_only: bool = False
    # The folder holds add-ons (mods, DLC, ...) and nothing that installs the base game.
    addons_only: bool = False
    media: dict[str, Any] | None = None
    # This user has a finished install whose cache is still on disk.
    installed: bool = False
    # When this user's newest saved version of the game was made (a game's saves are made as it is played).
    last_played: datetime | None = None

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


class ModSchema(BaseModel):
    """One mod: a top-level folder or archive of the game's mods folder."""

    name: str
    kind: str  # "folder" | "archive" | "file"
    size_bytes: int
    file_count: int


class ModsSchema(BaseModel):
    mods: list[ModSchema]


class ModJobSchema(BaseModel):
    """Where a mod's download stands: "idle" (nothing asked yet), "zipping", "ready" or "failed"."""

    name: str
    state: str
    bytes_done: int = 0
    bytes_total: int = 0
    error: str | None = None


class GameSizesSchema(BaseModel):
    """Disk the game takes on the server: its folder (the installers), the install caches and the saves."""

    installer_bytes: int
    cache_bytes: int
    saves_bytes: int
    total_bytes: int


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
