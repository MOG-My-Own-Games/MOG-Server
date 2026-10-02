# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Install session endpoints: detect a candidate, start/track/cancel a sandboxed
install, then stream or download the result.

Unlike RomM (install worker is a separate container behind Redis/RQ, gated on
`has_install_worker()`), MOG runs everything in this one process: starting a
session just submits to runner.enqueue_install's thread pool directly, and the
VNC proxy below talks to 127.0.0.1 instead of a worker hostname.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Annotated

import aiohttp
from fastapi import APIRouter, HTTPException, Path as PathVar, Request, Response, WebSocket, status
from starlette.background import BackgroundTask
from starlette.responses import FileResponse, StreamingResponse
from starlette.websockets import WebSocketState

from config import INSTALL_MAX_CONCURRENCY
from endpoints.responses.install import (
    InstallAutoModeForm,
    InstallCacheClearSchema,
    InstallCacheEntrySchema,
    InstallCacheSchema,
    InstallCandidateSchema,
    InstallCandidatesSchema,
    InstallDefaultsSchema,
    InstallFileSchema,
    InstallFilesSchema,
    InstallSessionSchema,
    InstallStartForm,
    InstallStreamFileSchema,
    InstallStreamManifestSchema,
    ProtonBuildSchema,
    ProtonBuildsSchema,
)
from handler.auth import AdminUser, CurrentUser
from handler.database import db_game_handler, db_install_session_handler
from handler.filesystem import fs_game_handler
from handler.filesystem.installer_detection import ARCHIVE_SOURCE_KINDS, pick_default_installer
from handler.install import bandwidth
from handler.install.defaults import default_auto_mode, default_manual_mode
from handler.install.archive_prescan import is_archive_candidate, list_source_candidates
from handler.install.manifest import (
    find_manifest_entry,
    live_view_of_final_manifest,
    read_live_manifest,
    read_manifest,
)
from handler.install.proton_builds import get_download_progress, is_extracting, list_proton_builds, remove_build
from handler.install.proton_builds import enqueue_download as enqueue_proton_download
from handler.install.runner import cancel_install as runner_cancel_install
from handler.install.runner import enqueue_install
from logger.logger import log
from models.install_session import ACTIVE_INSTALL_STATES, RUNNING_INSTALL_STATES, InstallSession, InstallSessionState
from utils.install_cache import (
    cache_root_dirs,
    clear_session_cache,
    dir_size_bytes,
    purge_superseded_sessions,
    resolve_expires_at,
    session_cache_dir,
)

router = APIRouter(prefix="/games", tags=["install"])

# VNC proxy only ever talks to this same process/container.
_VNC_HOST = "127.0.0.1"

_CONCURRENCY_LOCK = threading.Lock()


def _session_schema(session: InstallSession) -> InstallSessionSchema:
    return InstallSessionSchema.model_validate(session)


def _resolve_session(game_id: int, user_id: int, session_id: int | None) -> InstallSession | None:
    """The session a read endpoint should serve: a specific one if
    `session_id` is given (pinning a streaming client to the exact attempt
    it started, even if a newer one for the same game starts meanwhile),
    otherwise the latest for this game+user."""
    if session_id is not None:
        session = db_install_session_handler.get_session(session_id)
        if session is None or session.game_id != game_id or session.user_id != user_id:
            return None
        return session
    return db_install_session_handler.get_latest_session_for_game(game_id, user_id)


def _pick_reusable_session(sessions: list[InstallSession]) -> InstallSession | None:
    """The session that owns this game's install cache: the newest one whose
    cache directory exists, preferring a finished install over a partial one."""
    with_cache = [
        x
        for x in sessions
        if session_cache_dir(x.id).is_dir()
        or x.state in (InstallSessionState.DETECTING, InstallSessionState.AWAITING_INSTALLER)
    ]
    if not with_cache:
        return None
    with_cache.sort(key=lambda x: (x.state == InstallSessionState.DONE, x.created_at, x.id), reverse=True)
    return with_cache[0]


@router.get("/install/active")
async def get_active_installs(user: CurrentUser) -> list[InstallSessionSchema]:
    """This user's installs currently in progress - backs the "active
    installs" widget above the library list."""
    sessions = db_install_session_handler.get_dashboard_sessions_for_user(user.id)
    return [_session_schema(s) for s in sessions if s.state in ACTIVE_INSTALL_STATES]


@router.get("/install/defaults")
async def get_install_defaults(user: CurrentUser) -> InstallDefaultsSchema:
    """What a start request that omits auto_mode/manual_mode gets, so a client
    can prefill its own toggles with the server's Settings."""
    return InstallDefaultsSchema(auto_mode=default_auto_mode(), manual_mode=default_manual_mode())


@router.get("/{id}/install/candidates")
async def get_install_candidates(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], source: str | None = None
) -> InstallCandidatesSchema:
    """Detect installer candidates inside a game's directory.

    With `source` (an archive or disc image from the game's own candidates),
    lists the executables inside it instead, read from its member listing
    without extracting anything.
    """
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if source is not None:
        try:
            source_abs = Path(fs_game_handler.resolve_installer_abs_path(game, source))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        if not is_archive_candidate(source_abs):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source is not an archive or disc image")
        candidates = await asyncio.to_thread(list_source_candidates, source_abs)
    else:
        candidates = fs_game_handler.get_installer_candidates(game)

    return InstallCandidatesSchema(
        game_id=game.id,
        candidates=[
            InstallCandidateSchema(path=c.path, file_name=c.file_name, file_size_bytes=c.file_size_bytes, rank=c.rank, kind=c.kind)
            for c in candidates
        ],
        needs_manual_pick=len(candidates) == 0,
    )


@router.post("/{id}/install")
async def start_install_session(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], data: InstallStartForm
) -> InstallSessionSchema:
    """Create (or return) the install session for a game.

    Idempotent: an already-running session (INSTALLING/STREAMING) is
    returned as-is. With no installer_path/source_path, the top-ranked
    candidate is picked automatically, unpacking an archive/disc image
    first if needed. A game with no candidate at all, or
    `manual_mode=True`, sits in AWAITING_INSTALLER - a client should then
    show the VNC view and let the user pick a file by hand before retrying.
    """
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    game_sessions = db_install_session_handler.get_sessions_for_game(game.id)
    running = next((x for x in game_sessions if x.state in RUNNING_INSTALL_STATES), None)
    if running:
        return _session_schema(running)

    own_sessions = [x for x in game_sessions if x.user_id == user.id]
    existing = _pick_reusable_session(own_sessions)

    installer_path = data.installer_path
    source_path = data.source_path
    manual_mode = data.manual_mode if data.manual_mode is not None else default_manual_mode()
    if installer_path is None and source_path is None:
        candidates = fs_game_handler.get_installer_candidates(game)
        default = pick_default_installer(candidates)
        if default is not None:
            if default.kind in ARCHIVE_SOURCE_KINDS:
                source_path = default.path
            else:
                installer_path = default.path
    needs_manual_pick = manual_mode or (not installer_path and not source_path)

    initial_state = InstallSessionState.AWAITING_INSTALLER if needs_manual_pick else InstallSessionState.DETECTING
    previous_state = existing.state if existing else None
    auto_mode = data.auto_mode if data.auto_mode is not None else default_auto_mode()

    if existing:
        session = db_install_session_handler.update_session(
            existing.id,
            {
                "user_id": user.id,
                "installer_path": installer_path,
                "source_path": source_path,
                "phase": None,
                "phase_detail": None,
                "proton_build": data.proton_build,
                "auto_mode": auto_mode,
                "manual_mode": manual_mode,
                "auto_status": None,
                "auto_detail": None,
                "state": initial_state,
                "error": None,
                "vnc_url": None,
                "vnc_web_port": None,
                "vnc_token": None,
                "expires_at": resolve_expires_at(data.ttl_seconds),
            },
        )
    else:
        session = db_install_session_handler.add_session(
            InstallSession(
                game_id=game.id,
                user_id=user.id,
                state=initial_state,
                installer_path=installer_path,
                source_path=source_path,
                proton_build=data.proton_build,
                auto_mode=auto_mode,
                manual_mode=manual_mode,
                expires_at=resolve_expires_at(data.ttl_seconds),
            )
        )
    assert session is not None

    def _abandon() -> None:
        if existing and previous_state is not None:
            db_install_session_handler.update_session(session.id, {"state": previous_state})
        else:
            db_install_session_handler.delete_session(session.id)

    purge_superseded_sessions(game.id, session.id, user.id)

    if needs_manual_pick:
        return _session_schema(session)

    # A short critical section around count-then-transition, so two
    # concurrent start requests can't both see room under
    # INSTALL_MAX_CONCURRENCY and together exceed it.
    if not _CONCURRENCY_LOCK.acquire(timeout=10.0):
        _abandon()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many concurrent installs")
    try:
        if db_install_session_handler.count_running_sessions() >= INSTALL_MAX_CONCURRENCY:
            _abandon()
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many concurrent installs")
        session = db_install_session_handler.update_session(session.id, {"state": InstallSessionState.INSTALLING})
        assert session is not None
        enqueue_install(session.id)
    finally:
        _CONCURRENCY_LOCK.release()

    return _session_schema(session)


@router.get("/{id}/install")
async def get_install_session(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None
) -> InstallSessionSchema:
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _session_schema(session)


@router.patch("/{id}/install/auto-mode")
async def set_install_auto_mode(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], data: InstallAutoModeForm, session_id: int | None = None
) -> InstallSessionSchema:
    session = _resolve_session(id, user.id, session_id)
    if session is None or session.state not in ACTIVE_INSTALL_STATES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    updated = db_install_session_handler.update_session(session.id, {"auto_mode": data.enabled})
    assert updated is not None
    return _session_schema(updated)


@router.post("/{id}/install/cancel")
async def cancel_install(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None
) -> InstallSessionSchema:
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    runner_cancel_install(session.id)
    clear_session_cache(session.id)
    updated = db_install_session_handler.update_session(
        session.id,
        {
            "state": InstallSessionState.FAILED,
            "error": "Cancelled",
            "vnc_url": None,
            "vnc_web_port": None,
            "vnc_token": None,
        },
    )
    assert updated is not None
    return _session_schema(updated)


@router.delete("/{id}/install")
async def clear_install(user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None) -> None:
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if session.state in RUNNING_INSTALL_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Install is still running")
    clear_session_cache(session.id)
    db_install_session_handler.delete_session(session.id)


@router.get("/{id}/install/stream/manifest")
async def get_install_stream_manifest(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None
) -> InstallStreamManifestSchema:
    """Live view of an install's output, whether it's still running or
    already finished - lets a client poll ONE endpoint regardless of state.
    """
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    cache_dir = session_cache_dir(session.id)
    live_entries = read_live_manifest(cache_dir)
    if live_entries is None:
        final_entries = read_manifest(cache_dir)
        if final_entries is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        live_entries = live_view_of_final_manifest(final_entries)

    return InstallStreamManifestSchema(
        game_id=id,
        files=[
            InstallStreamFileSchema(path=e.path, size_bytes=e.size_bytes, sealed_bytes=e.sealed_bytes, complete=e.complete)
            for e in live_entries.values()
        ],
    )


_STREAM_PIECE_SIZE = 256 * 1024


def _parse_range(range_header: str | None, sealed_bytes: int) -> tuple[int, int] | tuple[None, None]:
    """Clamp a Range request to the hash-verified prefix of a still-growing
    file (``[0, sealed_bytes)``). No Range header defaults to serving from
    the start. ``(None, None)`` means nothing sealed is left to give for the
    requested range - the caller responds 416, and a client's own retry/
    backoff loop naturally waits for more of the file to seal."""
    start = 0
    end = sealed_bytes - 1
    if range_header and range_header.startswith("bytes="):
        spec = range_header[len("bytes=") :].split(",")[0].strip()
        range_start, _, range_end = spec.partition("-")
        if range_start:
            try:
                start = int(range_start)
            except ValueError:
                start = 0
        if range_end:
            try:
                end = min(end, int(range_end))
            except ValueError:
                pass
    if sealed_bytes <= 0 or start > end:
        return None, None
    return start, end


@router.get("/{id}/install/stream/{file_path:path}")
async def download_install_stream_file(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], file_path: str, request: Request, session_id: int | None = None
) -> Response:
    """Download one file from an install, live if it's still running.

    Once the file is complete this behaves exactly like
    `GET .../install/files/{path}`. While it's still being written, this
    instead serves a Range request clamped to the sealed (hash-verified)
    portion so far: a request past what's sealed yet gets 416, and any
    client's own retry/backoff loop naturally waits for more of the file to
    seal before trying again.
    """
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    cache_dir = session_cache_dir(session.id)
    live_entries = read_live_manifest(cache_dir)
    entry = live_entries.get(file_path) if live_entries else None

    if entry is None:
        final_entries = read_manifest(cache_dir)
        final_entry = find_manifest_entry(final_entries, file_path) if final_entries else None
        if not final_entry:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            path=cache_dir / final_entry.path, filename=Path(final_entry.path).name, media_type="application/octet-stream"
        )

    start, end = _parse_range(request.headers.get("range"), entry.sealed_bytes)
    if start is None:
        return Response(status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE, headers={"Retry-After": "1"})

    file_on_disk = cache_dir / entry.path
    content_length = end - start + 1

    async def body_stream():
        remaining = content_length
        f = await asyncio.to_thread(file_on_disk.open, "rb")
        try:
            await asyncio.to_thread(f.seek, start)
            while remaining > 0:
                piece = await asyncio.to_thread(f.read, min(remaining, _STREAM_PIECE_SIZE))
                if not piece:
                    break
                await bandwidth.acquire(len(piece))
                remaining -= len(piece)
                yield piece
        finally:
            await asyncio.to_thread(f.close)

    return StreamingResponse(
        body_stream(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type="application/octet-stream",
        headers={"Content-Range": f"bytes {start}-{end}/*", "Accept-Ranges": "bytes", "Content-Length": str(content_length)},
    )


@router.get("/{id}/install/files")
async def get_install_files(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None
) -> InstallFilesSchema:
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    entries = read_manifest(session_cache_dir(session.id))
    if entries is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return InstallFilesSchema(
        game_id=id, files=[InstallFileSchema(path=e.path, size_bytes=e.size_bytes, sha1=e.sha1) for e in entries]
    )


@router.get("/{id}/install/files/{file_path:path}")
async def download_install_file(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], file_path: str, session_id: int | None = None
) -> FileResponse:
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    entries = read_manifest(session_cache_dir(session.id))
    entry = find_manifest_entry(entries, file_path) if entries else None
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(
        path=session_cache_dir(session.id) / entry.path, filename=Path(entry.path).name, media_type="application/octet-stream"
    )


@router.get("/{id}/install/download")
async def download_install_zip(
    user: CurrentUser, id: Annotated[int, PathVar(ge=1)], session_id: int | None = None
) -> FileResponse:
    """The whole finished install cache as one ZIP - built to a temp file
    (not in memory, a multi-GB game would exhaust it) and removed once the
    response has been sent."""
    session = _resolve_session(id, user.id, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    entries = read_manifest(session_cache_dir(session.id))
    if not entries:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    cache_dir = session_cache_dir(session.id)
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()

    def build_zip() -> None:
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in entries:
                zf.write(cache_dir / entry.path, arcname=entry.path)

    await asyncio.to_thread(build_zip)
    return FileResponse(
        path=tmp.name,
        filename=f"game-{id}-install.zip",
        media_type="application/zip",
        background=BackgroundTask(os.unlink, tmp.name),
    )


# --- Proton builds ---


@router.get("/install/proton-builds")
async def get_proton_builds(user: CurrentUser) -> ProtonBuildsSchema:
    builds = await asyncio.to_thread(list_proton_builds)
    return ProtonBuildsSchema(
        builds=[ProtonBuildSchema(id=b.id, label=b.label, installed=b.installed, version=b.version, source=b.source) for b in builds]
    )


@router.post("/install/proton/{build_id}/download")
async def download_proton_build(user: AdminUser, build_id: str) -> dict:
    enqueue_proton_download(build_id)
    return {"build_id": build_id}


@router.get("/install/proton/{build_id}/progress")
async def proton_download_progress(user: CurrentUser, build_id: str) -> dict:
    return {"build_id": build_id, "progress": get_download_progress(build_id), "extracting": is_extracting(build_id)}


@router.delete("/install/proton/{build_id}")
async def remove_proton_build_route(user: AdminUser, build_id: str) -> None:
    await asyncio.to_thread(remove_build, build_id)


# --- Install cache management ---


@router.get("/install/cache")
async def list_install_cache(user: AdminUser) -> InstallCacheSchema:
    entries = []
    for path in cache_root_dirs():
        try:
            session_id = int(path.name)
        except ValueError:
            continue
        session = db_install_session_handler.get_session(session_id)
        entries.append(
            InstallCacheEntrySchema(
                session_id=session_id,
                game_id=session.game_id if session else None,
                state=session.state if session else None,
                size_bytes=dir_size_bytes(path),
            )
        )
    return InstallCacheSchema(entries=entries)


@router.delete("/install/cache")
async def clear_all_install_cache(user: AdminUser) -> InstallCacheClearSchema:
    """Delete every cache not currently in use. Running installs are
    skipped (see RUNNING_INSTALL_STATES), not interrupted."""
    cleared = 0
    for path in cache_root_dirs():
        try:
            session_id = int(path.name)
        except ValueError:
            continue
        session = db_install_session_handler.get_session(session_id)
        if session is not None and session.state in RUNNING_INSTALL_STATES:
            continue
        clear_session_cache(session_id)
        if session is not None:
            db_install_session_handler.delete_session(session_id)
        cleared += 1
    return InstallCacheClearSchema(cleared=cleared)


@router.delete("/install/cache/{session_id}")
async def clear_one_install_cache(user: AdminUser, session_id: Annotated[int, PathVar(ge=1)]) -> None:
    session = db_install_session_handler.get_session(session_id)
    if session is not None and session.state in RUNNING_INSTALL_STATES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Install is still running")
    clear_session_cache(session_id)
    if session is not None:
        db_install_session_handler.delete_session(session_id)


# --- VNC proxy ---
#
# Gated on a per-session capability token (see models/install_session.py's
# vnc_token and handler/install/vnc.py's _build_public_url), not the usual
# Basic-auth user, so the VNC view can be embedded in an <iframe> - a real
# login prompt only ever fires for a top-level navigation, never inside an
# iframe's own request, so Basic auth alone would leave it stuck unauthenticated.
# The static-asset route (this one) only checks that *something* is
# legitimately live on the port - noVNC's own JS/CSS has nothing sensitive in
# it, and relative asset URLs never carry the page's own query string
# (where the token lives) anyway. The actual sensitive channel (remote
# control + live pixels) is the websocket below, which does check the token.

_VNC_PROXY_DROP_REQUEST_HEADERS = {"host", "content-length", "connection"}
_VNC_PROXY_DROP_RESPONSE_HEADERS = _VNC_PROXY_DROP_REQUEST_HEADERS | {"content-encoding", "server", "date", "transfer-encoding"}


@router.get("/install/vnc/{port}/{path:path}")
async def install_vnc_http(port: Annotated[int, PathVar(ge=1, le=65535)], path: str, request: Request) -> Response:
    """Proxy the noVNC static page/assets for a running install's VNC bridge.
    The pixel data itself goes over install_vnc_ws; this only ever serves
    vnc.html and its JS/CSS."""
    if db_install_session_handler.get_running_session_for_port(port) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    upstream_url = f"http://{_VNC_HOST}:{port}/{path}"
    forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in _VNC_PROXY_DROP_REQUEST_HEADERS}

    async with aiohttp.ClientSession() as client:
        try:
            upstream_response = await client.request(
                request.method, upstream_url, params=list(request.query_params.multi_items()), headers=forward_headers, data=await request.body()
            )
        except aiohttp.ClientError as e:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="VNC bridge unreachable") from e

        body = await upstream_response.read()
        response_headers = {k: v for k, v in upstream_response.headers.items() if k.lower() not in _VNC_PROXY_DROP_RESPONSE_HEADERS}
        # noVNC static assets only change when the image is rebuilt, but a
        # stale browser cache of an older ui.js/vnc.html causes confusing
        # crashes, since the cached JS can reference DOM elements the cached
        # HTML doesn't define.
        response_headers["Cache-Control"] = "no-store"
        return Response(content=body, status_code=upstream_response.status, headers=response_headers, media_type=upstream_response.content_type)


@router.websocket("/install/vnc/{port}/{path:path}")
async def install_vnc_ws(websocket: WebSocket, port: Annotated[int, PathVar(ge=1, le=65535)], path: str) -> None:
    """Proxy the actual VNC websocket (binary RFB-over-websocket frames via
    websockify) for a running install, gated on the session's own
    vnc_token (see this module's own note on the VNC proxy above)."""
    token = websocket.query_params.get("token")
    session = db_install_session_handler.get_running_session_for_port(port)
    if not token or session is None or session.vnc_token != token:
        await websocket.close(code=4403)
        return

    requested_protocols = [p.strip() for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    upstream_url = f"ws://{_VNC_HOST}:{port}/websockify"

    async with aiohttp.ClientSession() as client:
        try:
            async with client.ws_connect(
                upstream_url, params=list(websocket.query_params.multi_items()), protocols=requested_protocols or ()
            ) as upstream:
                await websocket.accept(subprotocol=upstream.protocol)

                async def client_to_upstream():
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("bytes") is not None:
                            await upstream.send_bytes(message["bytes"])
                        elif message.get("text") is not None:
                            await upstream.send_str(message["text"])

                async def upstream_to_client():
                    async for message in upstream:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(message.data)
                        elif message.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(message.data)
                        else:
                            return

                _, pending = await asyncio.wait(
                    [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except aiohttp.ClientError:
            log.debug(f"Couldn't reach the VNC bridge on port {port}")
        finally:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
