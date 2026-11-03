from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from handler.filesystem.fs_tags import parse_fs_tags


class GameSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    library_id: int | None
    fs_name: str
    name: str
    summary: str | None
    igdb_id: int | None
    # The metadata in igdb_metadata is the record of the provider named here (`igdb`, `romm`, `steam`, `vndb`, `dlsite`, `itch`)
    # and that provider's id for the game is in provider_ids; null for a game with no match yet.
    metadata_source: str | None = None
    provider_ids: dict[str, Any] | None = None
    igdb_metadata: dict[str, Any] | None
    sgdb_id: int | None
    # The title the game is a version of: games with the same key are one game (their time played, saves, name and artwork go
    # together). `igdb:<id>` when the IGDB id says so, else `mog:<id>`, which MOG gives a game by itself or that was set by hand.
    title_key: str = Field(validation_alias="effective_title_key")
    # Completion times in seconds; empty when the game is not scraped or HowLongToBeat had nothing.
    hltb_id: int | None = None
    hltb_metadata: dict[str, Any] | None = None
    cover_path: str | None
    # What the game's folder takes on the server's disk; None until measured.
    size_bytes: int | None = None
    missing_from_fs: bool = False
    # Added by a client to sync its saves, with no folder in any library: it has never had files here.
    imported: bool = False
    # Gone from disk, but some user still has saves of it, so it is kept (not "missing").
    saves_only: bool = False
    # The folder holds add-ons (mods, DLC, ...) and nothing that installs the base game.
    addons_only: bool = False
    # Something sits in the game's mods folder.
    has_mods: bool = False
    # When the game was first seen by a scan.
    created_at: datetime | None = None
    media: dict[str, Any] | None = None
    # This user has a finished install whose cache is still on disk.
    installed: bool = False
    # When this user's newest saved version of the game was made (a game's saves are made as it is played).
    last_played: datetime | None = None
    # The name of the machine that made that version, so where the game was last played.
    last_played_on: str | None = None
    # How long this user has played the game, in seconds, from the sessions their clients reported (0 when none). The time of
    # the whole title: every version says the same.
    playtime_seconds: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fs_tags(self) -> list[str]:
        return parse_fs_tags(self.fs_name)


class SavePathsSchema(BaseModel):
    # Where a Linux build of the game keeps its saves, from the Ludusavi manifest, with its placeholders
    # (`<xdgConfig>/Game/Saves`, `<home>/.game`, `<base>/saves`). Empty when the manifest does not know the game.
    paths: list[str]


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
    """Disk the game takes on the server: its folder (the installers), the install caches, the saves and the addons."""

    installer_bytes: int
    cache_bytes: int
    saves_bytes: int
    addons_bytes: int = 0
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


class GameMergeForm(BaseModel):
    """What the person chose in Merge...: `other_id` is the game that goes into this one. Only what is sent is changed."""

    other_id: int = Field(ge=1)
    # The values of the result (not "this" or "other": the person may have typed them).
    name: str | None = Field(default=None, min_length=1, max_length=400)
    summary: str | None = None
    # Whose provider ids the result has (the metadata of the provider follows its id).
    igdb_id: Literal["this", "other"] = "this"
    sgdb_id: Literal["this", "other"] = "this"
    # For each kind of artwork: this game's, the other's, or none.
    media: dict[str, Literal["this", "other", "none"]] = Field(default_factory=dict)
    # How the time played ends: both added up, this game's only, or the other's only.
    playtime: Literal["sum", "this", "other"] = "sum"


class GameMergePreviewSchema(BaseModel):
    # "merge": the other game goes into this one. "link": both have files, so they stay as versions of one game.
    mode: Literal["merge", "link"]
    # Why it cannot be done now (`install_in_progress`), else None.
    conflict: str | None = None
    this: GameSchema
    other: GameSchema
    # What hangs on each game, all users together: playtime_seconds, play_sessions, save_versions, installs, addons, launch_settings.
    counts: dict[str, dict[str, int]]


class LibrarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    root_path: str


class GameImportForm(BaseModel):
    # What the game is called; the same name (ignoring case and punctuation) is the same imported game.
    name: str = Field(min_length=1, max_length=400)
    # The library the game is listed under. Nothing is written into the library's folder: the game is only filed there.
    library_id: int | None = None


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
