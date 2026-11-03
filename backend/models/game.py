"""A Game is one top-level entry (file or directory) under a Library's root_path.
Replaces RomM's models/rom.py: no platform, no per-provider hash matching, no
retro-emulation fields - just enough to identify it on disk and show it in a
library, plus IGDB/SteamGridDB metadata (see handler/metadata)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import BaseModel
from utils.database import CustomJSON

if TYPE_CHECKING:
    from models.install_session import InstallSession
    from models.library import Library

NAME_MAX_LENGTH = 400
FS_NAME_MAX_LENGTH = 450


# Which provider's id names the title of a game that IGDB does not know, and whose record is its metadata when several have it (a test
# keeps it the same as handler.scrape_handler.PRIORITY).
PROVIDER_PRIORITY = ("igdb", "romm", "steam", "vndb", "dlsite", "itch")


class Game(BaseModel):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # None for a game a client imported: it has no folder in any library (yet).
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), default=None)

    # Name of the file or directory directly under the library's root_path.
    fs_name: Mapped[str] = mapped_column(String(length=FS_NAME_MAX_LENGTH))

    # Display name: defaults to fs_name, overridable once metadata is matched.
    name: Mapped[str] = mapped_column(String(length=NAME_MAX_LENGTH))
    summary: Mapped[str | None] = mapped_column(Text(), default=None)

    igdb_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    igdb_metadata: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)
    sgdb_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    # Which title the game is a version of, when that was decided and not told by the IGDB id (`mog:<id>`, see handler/titles.py).
    title_key: Mapped[str | None] = mapped_column(String(length=40), default=None)
    # Which provider the metadata in igdb_metadata came from, and each provider's id for this game ({"igdb": 42, "vndb": 17}).
    # igdb_metadata keeps its name for every source: it holds the record of whichever match is in use.
    metadata_source: Mapped[str | None] = mapped_column(String(length=20), default=None)
    provider_ids: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)
    hltb_id: Mapped[int | None] = mapped_column(Integer(), default=None)
    # Completion times in seconds (main_story, main_plus_extra, completionist, all_styles) and their report counts.
    hltb_metadata: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)
    # Providers that found nothing for this game, as {provider: ISO time of the search} (see handler/scrape_handler.py).
    provider_misses: Mapped[dict[str, str] | None] = mapped_column(CustomJSON(), default=dict)
    # Artwork kinds (cover, banner...) a person removed on purpose: the automatic scrape leaves them empty (an explicit one fills them).
    media_cleared: Mapped[list[str] | None] = mapped_column(CustomJSON(), default=list)
    cover_path: Mapped[str | None] = mapped_column(String(length=1000), default=None)
    # The artwork chosen for each kind (see handler/media.py): {kind: {"url": ..., "source": ...}}.
    # cover_path mirrors media["cover"] for what reads it directly.
    media: Mapped[dict[str, Any] | None] = mapped_column(CustomJSON(), default=dict)

    # Set by a scan when the file/directory is gone from disk; cleared if it reappears.
    missing_from_fs: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")
    # Set by a scan when the folder holds add-on folders (mods, DLC, ...) and nothing that installs
    # the base game (see installer_detection.only_addons).
    addons_only: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")

    # Set by a scan when the game's mods folder holds something (see handler/mods.py has_mods).
    has_mods: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")

    # A client added the game to sync its saves and no scan has found its folder yet: it has no files here. A scan that
    # finds a folder named like it takes the entry over and clears this (see scan_handler).
    imported: Mapped[bool] = mapped_column(Boolean(), default=False, server_default="0")

    # What the game's own folder (or file) takes on disk, set by a scan and refreshed whenever the size is
    # measured again (handler/sizes.py). NULL until measured.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger(), default=None)

    # Plain lazy loading for Phase 1 simplicity (RomM uses lazy="raise" + explicit
    # eager-loading everywhere to catch N+1s; worth adopting once this grows).
    library: Mapped["Library"] = relationship(lazy="joined", back_populates="games")
    install_sessions: Mapped[list["InstallSession"]] = relationship(
        lazy="select", back_populates="game", cascade="all, delete-orphan"
    )

    @property
    def effective_title_key(self) -> str:
        """The title this game is a version of (see handler/titles.py): games with the same one are one game."""
        if self.title_key:
            return self.title_key
        if self.igdb_id:
            return f"igdb:{self.igdb_id}"
        ids = self.provider_ids or {}
        key = next((key for key in PROVIDER_PRIORITY if ids.get(key)), None)
        return f"{key}:{ids[key]}" if key else f"mog:{self.id}"

    def __repr__(self) -> str:
        return f"Game({self.id} {self.name!r})"
