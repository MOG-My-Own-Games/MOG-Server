from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from handler.auth import AdminUser, CurrentUser
from handler.database import db_game_handler, db_install_session_handler, db_saves_handler
from handler.filesystem import fs_game_handler
from handler.filesystem.installer_detection import category_for_path
from handler import media as media_handler
from handler.metadata import igdb_handler, sgdb_handler
from handler.saves import purge_game
from handler.scrape_handler import refresh_game, search_name
from models.install_session import InstallSessionState
from starlette.concurrency import run_in_threadpool
from utils.image_cache import COVER_MAX_HEIGHT, cached_image, media_type
from utils.install_cache import session_cache_dir

from endpoints.responses.game import (
    GameFileSchema,
    GameFilesSchema,
    GameSchema,
    GameSizeSchema,
    GameUpdateForm,
    MediaSelectionForm,
)

router = APIRouter(prefix="/games", tags=["games"])


def _installed_game_ids(user_id: int) -> set[int]:
    return {
        s.game_id
        for s in db_install_session_handler.get_dashboard_sessions_for_user(user_id)
        if s.state == InstallSessionState.DONE and session_cache_dir(s.id).is_dir()
    }


@router.get("")
async def list_games(user: CurrentUser, library_id: int | None = None) -> list[GameSchema]:
    if library_id is not None:
        if not user.is_admin and library_id in user.hidden_library_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        games = db_game_handler.get_games_for_library(library_id)
    else:
        games = db_game_handler.get_all_games()
        if not user.is_admin and user.hidden_library_ids:
            games = [g for g in games if g.library_id not in user.hidden_library_ids]
    installed = _installed_game_ids(user.id)
    with_saves = db_saves_handler.game_ids_with_saves()
    return [
        GameSchema.model_validate(g).model_copy(
            update={"installed": g.id in installed, "saves_only": g.missing_from_fs and g.id in with_saves}
        )
        for g in games
    ]


@router.get("/missing")
async def list_missing_games(user: AdminUser) -> list[GameSchema]:
    """Games a scan could not find, the ones that still have saves flagged `saves_only`."""
    with_saves = db_saves_handler.game_ids_with_saves()
    return [
        GameSchema.model_validate(g).model_copy(update={"saves_only": g.id in with_saves})
        for g in db_game_handler.get_missing_games()
    ]


@router.delete("/missing")
async def clear_missing_games(user: AdminUser) -> dict:
    """Forget the missing games; those that still have saves stay (delete one to remove its saves too)."""
    kept = len(db_saves_handler.game_ids_with_saves() & {g.id for g in db_game_handler.get_missing_games()})
    return {"cleared": db_game_handler.delete_missing_games(), "kept_with_saves": kept}


@router.delete("/{id}")
async def delete_missing_game(
    user: AdminUser, id: Annotated[int, Path(ge=1)], delete_saves: bool = False
) -> None:
    """Only for games a scan flagged as missing: a present game would just be re-added.

    A game that still has saves is refused with 409 `has_saves` (and what would be lost) unless
    `delete_saves` says to delete those too."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not game.missing_from_fs:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Game is not missing from disk")
    versions, size_bytes = db_saves_handler.summary(game_id=id)
    if versions and not delete_saves:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "has_saves", "versions": versions, "size_bytes": size_bytes},
        )
    await run_in_threadpool(purge_game, id)
    db_game_handler.delete_game(id)


@router.get("/{id}")
async def get_game(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> GameSchema:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    installed = game.id in _installed_game_ids(user.id)
    saves_only = game.missing_from_fs and game.id in db_saves_handler.game_ids_with_saves()
    return GameSchema.model_validate(game).model_copy(update={"installed": installed, "saves_only": saves_only})


def _serve_image(url: str | None, max_height: int | None = None) -> FileResponse:
    path = cached_image(url, max_height) if url else None
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return FileResponse(path, media_type=media_type(path), headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{id}/cover")
async def get_game_cover(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> FileResponse:
    """The cover through this server (cached, shrunk), for clients that cannot reach the CDN quickly."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    url = game.cover_path or ((game.igdb_metadata or {}).get("cover") or {}).get("url")
    return await run_in_threadpool(_serve_image, url, COVER_MAX_HEIGHT)


def _chosen_url(game, kind: str) -> str | None:
    url = ((game.media or {}).get(kind) or {}).get("url")
    if url is None and kind == "cover":
        url = game.cover_path or ((game.igdb_metadata or {}).get("cover") or {}).get("url")
    return url


@router.get("/{id}/media/candidates")
async def get_media_candidates(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> dict:
    """Every artwork the providers offer for this game, by kind, with the current choice and the
    providers' own default for each: what the scrape dialog shows."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    found = await run_in_threadpool(media_handler.candidates, game)
    return {"candidates": found, "defaults": media_handler.defaults(found), "selected": game.media or {}}


@router.put("/{id}/media")
async def choose_media(user: AdminUser, id: Annotated[int, Path(ge=1)], data: MediaSelectionForm) -> GameSchema:
    """Set the artwork used for each kind named in the body (null clears it). Only images from
    the providers' own hosts are accepted."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    media = dict(game.media or {})
    update: dict = {}
    for kind, url in data.model_dump(exclude_unset=True).items():
        if url is None:
            media.pop(kind, None)
            if kind == "cover":
                update["cover_path"] = None
            continue
        chosen = media_handler.selection(url)
        if chosen is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{kind}: not an image from a known provider")
        media[kind] = chosen
        if kind == "cover":
            update["cover_path"] = url
    updated = db_game_handler.update_game(id, {**update, "media": media})
    return GameSchema.model_validate(updated)


@router.get("/{id}/media/{kind}")
async def get_game_media(user: CurrentUser, id: Annotated[int, Path(ge=1)], kind: str) -> FileResponse:
    """One kind of the game's artwork through this server (cached), for clients building their own entries."""
    if kind not in media_handler.KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await run_in_threadpool(_serve_image, _chosen_url(game, kind), COVER_MAX_HEIGHT if kind == "cover" else None)


@router.get("/{id}/screenshots/{index}")
async def get_game_screenshot(
    user: CurrentUser, id: Annotated[int, Path(ge=1)], index: Annotated[int, Path(ge=0)]
) -> FileResponse:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    shots = (game.igdb_metadata or {}).get("screenshots") or []
    url = shots[index].get("url") if index < len(shots) else None
    return await run_in_threadpool(_serve_image, url, None)


@router.get("/{id}/size")
async def get_game_size(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> GameSizeSchema:
    """What the game's folder (or file) takes on the server's disk."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    detected = await run_in_threadpool(fs_game_handler.list_game_files_flat, game)
    return GameSizeSchema(size_bytes=sum(f.size_bytes for f in detected), file_count=len(detected))


@router.get("/{id}/files")
async def get_game_files(user: CurrentUser, id: Annotated[int, Path(ge=1)]) -> GameFilesSchema:
    """The game's own files in its library (not the install cache)."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    detected = await run_in_threadpool(fs_game_handler.list_game_files_flat, game)
    return GameFilesSchema(
        root_path=str(fs_game_handler.get_game_root_abs_path(game)),
        files=[
            GameFileSchema(path=f.path, size_bytes=f.size_bytes, category=category_for_path(PurePosixPath(f.path)))
            for f in sorted(detected, key=lambda f: f.path.lower())
        ],
    )


@router.put("/{id}")
async def update_game(user: AdminUser, id: Annotated[int, Path(ge=1)], data: GameUpdateForm) -> GameSchema:
    """Manual metadata edit, for a field a scrape got wrong or when there's
    no provider match to scrape at all. Changing the match (name, igdb_id or
    sgdb_id) re-scrapes everything else from the providers; fields edited in
    the same request win over the re-scraped values."""
    current = db_game_handler.get_game(id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    body = data.model_dump(exclude_unset=True)
    changed = {k for k, v in body.items() if getattr(current, k) != v}
    rematch = changed & {"name", "igdb_id", "sgdb_id"}
    if rematch:
        # An id left untouched while the name or the other id changed is stale.
        if "igdb_id" not in rematch:
            body["igdb_id"] = None
        if "sgdb_id" not in rematch:
            body["sgdb_id"] = None

    game = db_game_handler.update_game(id, body)
    if rematch:
        await run_in_threadpool(refresh_game, game, frozenset(changed | (body.keys() & {"cover_path"})))
        game = db_game_handler.get_game(id)
    return GameSchema.model_validate(game)


@router.post("/{id}/scrape")
async def scrape_one_game(user: AdminUser, id: Annotated[int, Path(ge=1)]) -> GameSchema:
    """Re-fetch this game's metadata and cover from the providers, replacing what is
    stored (by its IGDB/SteamGridDB id when set, otherwise by name)."""
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    applied = await run_in_threadpool(refresh_game, game, frozenset(), True)
    if not applied:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Nothing could be fetched from IGDB or SteamGridDB (check the provider keys in Settings)",
        )
    return GameSchema.model_validate(db_game_handler.get_game(id))


@router.get("/{id}/metadata/igdb/search")
async def search_igdb(
    user: AdminUser, id: Annotated[int, Path(ge=1)], query: Annotated[str | None, Query()] = None
) -> list[dict]:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await run_in_threadpool(igdb_handler.search_games, query or search_name(game.name))


@router.post("/{id}/metadata/igdb/{igdb_id}")
async def apply_igdb_match(user: AdminUser, id: Annotated[int, Path(ge=1)], igdb_id: int) -> GameSchema:
    """Apply one IGDB search result. Re-fetches the full record by id first
    (search results can omit fields IGDB didn't populate in list context -
    see igdb_handler.get_game_by_id) so igdb_metadata always ends up with
    everything available, not just the trimmed shape search_games returns."""
    full = await run_in_threadpool(igdb_handler.get_game_by_id, igdb_id)
    if full is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not fetch this game from IGDB")
    game = db_game_handler.update_game(
        id, {"igdb_id": igdb_id, "name": full.get("name"), "summary": full.get("summary"), "igdb_metadata": full}
    )
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    game = db_game_handler.update_game(id, {"sgdb_id": None})
    await run_in_threadpool(refresh_game, game, frozenset({"name", "summary"}))
    return GameSchema.model_validate(db_game_handler.get_game(id))


@router.get("/{id}/metadata/sgdb/search")
async def search_sgdb(
    user: AdminUser, id: Annotated[int, Path(ge=1)], query: Annotated[str | None, Query()] = None
) -> list[str]:
    game = db_game_handler.get_game(id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await run_in_threadpool(sgdb_handler.search_grids, query or search_name(game.name))


@router.post("/{id}/metadata/sgdb")
async def apply_sgdb_cover(user: AdminUser, id: Annotated[int, Path(ge=1)], cover_path: str) -> GameSchema:
    current = db_game_handler.get_game(id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    update: dict = {"cover_path": cover_path}
    chosen = media_handler.selection(cover_path)
    if chosen is not None:  # the cover picked here is the game's chosen cover
        update["media"] = {**(current.media or {}), "cover": chosen}
    game = db_game_handler.update_game(id, update)
    return GameSchema.model_validate(game)
