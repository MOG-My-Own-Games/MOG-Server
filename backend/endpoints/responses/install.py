# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from models.install_session import InstallSessionState


class InstallCandidateSchema(BaseModel):
    path: str
    file_name: str
    file_size_bytes: int
    rank: int
    kind: str


class InstallCandidatesSchema(BaseModel):
    game_id: int
    candidates: list[InstallCandidateSchema]
    needs_manual_pick: bool


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
    auto_status: str | None
    auto_detail: str | None
    vnc_url: str | None
    cache_path: str | None
    bytes_written: int
    bytes_total: int
    error: str | None


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


class InstallCacheSchema(BaseModel):
    entries: list[InstallCacheEntrySchema]


class InstallCacheClearSchema(BaseModel):
    cleared: int


class InstallDefaultsSchema(BaseModel):
    """The modes a start request that omits them gets (the server's Settings)."""

    auto_mode: bool
    manual_mode: bool
