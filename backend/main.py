from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import DEV_MODE, RESOURCES_BASE_PATH
from endpoints import games, install, libraries, settings, users
from logger.logger import log
from startup import run_startup_tasks

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_startup_tasks()
    log.info("MOG Server started")
    yield


app = FastAPI(title="MOG Server", version="0.1.0", lifespan=lifespan)

app.include_router(libraries.router, prefix="/api")
app.include_router(games.router, prefix="/api")
app.include_router(install.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(users.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


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
