# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from endpoints.responses.save import UtcDatetime
from models.install_session import InstallSessionState
from models.install_step import INSTALL_STEP_PATH_MAX_LENGTH, INSTALL_STEPS_MAX


class InstallCandidateSchema(BaseModel):
    path: str
    file_name: str
    file_size_bytes: int
    rank: int
    kind: str
    category: str = "game"


class InstallStepSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    path: str
    source_path: str | None = None


class InstallStepForm(BaseModel):
    path: str = Field(min_length=1, max_length=INSTALL_STEP_PATH_MAX_LENGTH)
    source_path: str | None = Field(default=None, min_length=1, max_length=INSTALL_STEP_PATH_MAX_LENGTH)


class InstallOrderForm(BaseModel):
    """The whole order of a game's installers, first one the game's own; it replaces the saved one."""

    steps: list[InstallStepForm] = Field(max_length=INSTALL_STEPS_MAX)


class InstallOrderSchema(BaseModel):
    game_id: int
    steps: list[InstallStepSchema]


class InstallCandidatesSchema(BaseModel):
    game_id: int
    candidates: list[InstallCandidateSchema]
    needs_manual_pick: bool
    # The saved installer order of the game (see InstallOrderSchema), so a client can show it with the candidates.
    order: list[InstallStepSchema] = []
    # For a listing of what is inside an archive (`source`): nothing in it looks like an installer, so the
    # archive is most likely a game that needs none, and extracting it as it is would be the way to install it.
    extract_suggested: bool = False


class InstallSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    user_id: int
    state: InstallSessionState
    installer_path: str | None
    source_path: str | None
    proton_build: str | None
    phase: str | None
    phase_detail: str | None
    auto_mode: bool
    manual_mode: bool
    extract_only: bool
    auto_status: str | None
    auto_detail: str | None
    vnc_url: str | None
    cache_path: str | None
    bytes_written: int
    bytes_total: int
    error: str | None
    # For a `queued` session: where it stands, 1 being the next to start. None otherwise.
    queue_position: int | None = None
    # Which installer of a chain is running (0-based) and how many the session runs; 0 and 1 for a single installer.
    step_index: int = 0
    step_total: int = 1


class InstallQueueForm(BaseModel):
    """The waiting installs in the order they should start (ids of the sessions, the first to start first)."""

    session_ids: list[int]


class InstallStartForm(BaseModel):
    """Request body to start (or restart) an install session for a game."""

    installer_path: str | None = None
    source_path: str | None = None
    proton_build: str | None = None
    # Cache lifetime in seconds. ``None`` uses the configured default TTL,
    # a value <= 0 means unlimited (never auto-evict).
    ttl_seconds: int | None = None
    auto_mode: bool | None = None
    manual_mode: bool | None = None
    # Extract the source archive (or, with none, the game's own files) as it is into the install cache instead of
    # running an installer. Left out, an archive with no installer inside is extracted; false runs it anyway.
    extract_only: bool | None = None
    # The installers to run in this order, the first being the game's own and the rest patches. Left out, the saved
    # order of the game is used when there is one, else the installer picked as before. A chain is never extracted.
    steps: list[InstallStepForm] | None = Field(default=None, max_length=INSTALL_STEPS_MAX)


class InstallAutoModeForm(BaseModel):
    enabled: bool


class InstallStreamFileSchema(BaseModel):
    path: str
    size_bytes: int
    sealed_bytes: int
    complete: bool


class InstallStreamManifestSchema(BaseModel):
    game_id: int
    files: list[InstallStreamFileSchema]


class InstallFileSchema(BaseModel):
    path: str
    size_bytes: int
    sha1: str


class InstallFilesSchema(BaseModel):
    game_id: int
    files: list[InstallFileSchema]


class ProtonBuildSchema(BaseModel):
    id: str
    label: str
    installed: bool
    version: str | None
    source: str


class ProtonBuildsSchema(BaseModel):
    builds: list[ProtonBuildSchema]


class ProtonDownloadProgressSchema(BaseModel):
    build_id: str
    progress: float | None
    extracting: bool


class InstallCacheEntrySchema(BaseModel):
    session_id: int
    game_id: int | None
    state: InstallSessionState | None
    size_bytes: int
    # None: the cache never expires.
    expires_at: UtcDatetime | None = None


class ModCacheEntrySchema(BaseModel):
    game_id: int
    file_name: str
    size_bytes: int
    modified_at: float


class InstallCacheSchema(BaseModel):
    entries: list[InstallCacheEntrySchema]
    # The zipped mods kept for download, apart from the installers' caches above.
    mods: list[ModCacheEntrySchema] = []


class InstallCacheExpirySchema(BaseModel):
    expires_at: UtcDatetime | None


class InstallCacheClearSchema(BaseModel):
    cleared: int


class InstallDefaultsSchema(BaseModel):
    """The modes a start request that omits them gets (the server's Settings)."""

    auto_mode: bool
    manual_mode: bool
    download_workers: int
