from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import DEV_MODE, MOG_VERSION, RESOURCES_BASE_PATH
from endpoints import devices, games, install, libraries, notifications, saves, settings, users
from handler import library_watcher
from handler.metadata import ludusavi_handler
from logger.logger import log
from startup import run_startup_tasks

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_startup_tasks()
    stop_watching = library_watcher.start()
    stop_ludusavi = ludusavi_handler.start()
    log.info("MOG Server started")
    yield
    if stop_watching is not None:
        stop_watching.set()
    if stop_ludusavi is not None:
        stop_ludusavi.set()


app = FastAPI(title="MOG Server", version=MOG_VERSION, lifespan=lifespan)

app.include_router(devices.router, prefix="/api")
app.include_router(libraries.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(install.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(saves.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": MOG_VERSION}


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Always revalidate the web UI's own files, so a new version is never shadowed by a stale cached script."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api", "/resources")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# User-uploaded assets (avatars). Unauthenticated on purpose: a profile
# picture isn't sensitive, and gating it would require every <img> tag to
# somehow carry Basic-auth credentials (the same problem the VNC proxy's
# token solves for an <iframe> - not worth it here for a non-sensitive file).
Path(RESOURCES_BASE_PATH).mkdir(parents=True, exist_ok=True)
app.mount("/resources", StaticFiles(directory=RESOURCES_BASE_PATH), name="resources")

# Mounted last and at the root: API routes above are matched first, and
# anything left over (/, /style.css, /app.js) falls through to the static
# minimal web UI (see frontend/, and docs/TODO.md for the planned redesign).
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=DEV_MODE)  # noqa: S104 - bind-all is the point in a container
