#!/usr/bin/env python3
"""Dev-loop MCP server for MOG-Server.

Wraps a few read/trigger operations against a locally running MOG-Server
instance (default http://localhost:5000) so an agent iterating on this repo
doesn't have to hand-craft curl calls. Not a product surface - keep it small.

Requires MOG_ADMIN_USERNAME/MOG_ADMIN_PASSWORD (or MOG_MCP_USER/MOG_MCP_PASS)
in the environment to authenticate against the running server.
"""

from __future__ import annotations

import os
import subprocess

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.getenv("MOG_SERVER_URL", "http://localhost:5000")
_USER = os.getenv("MOG_MCP_USER") or os.getenv("MOG_ADMIN_USERNAME", "admin")
_PASS = os.getenv("MOG_MCP_PASS") or os.getenv("MOG_ADMIN_PASSWORD", "")

mcp = FastMCP("mog-server-dev")


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, auth=(_USER, _PASS), timeout=30)


@mcp.tool()
def health_check() -> dict:
    """Check whether the local MOG-Server instance is up."""
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        resp = c.get("/api/health")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_libraries() -> list[dict]:
    """List every configured library (id, name, root_path)."""
    with _client() as c:
        resp = c.get("/api/libraries")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def trigger_scan(library_id: int) -> dict:
    """Rescan one library's root_path and sync its games."""
    with _client() as c:
        resp = c.post(f"/api/libraries/{library_id}/scan")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_games(library_id: int | None = None) -> list[dict]:
    """List games, optionally filtered to one library."""
    with _client() as c:
        params = {"library_id": library_id} if library_id is not None else {}
        resp = c.get("/api/games", params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def get_install_session(game_id: int) -> dict:
    """Latest install session for a game (state, phase, bytes_written, ...)."""
    with _client() as c:
        resp = c.get(f"/api/games/{game_id}/install")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_active_installs() -> list[dict]:
    """Every install currently in progress, across all games."""
    with _client() as c:
        resp = c.get("/api/games/install/active")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def scrape_game(game_id: int) -> dict:
    """Best-effort IGDB + SteamGridDB metadata fill for one game."""
    with _client() as c:
        resp = c.post(f"/api/games/{game_id}/scrape")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def scrape_library(library_id: int) -> dict:
    """Best-effort metadata fill for every game in a library missing it."""
    with _client() as c:
        resp = c.post(f"/api/libraries/{library_id}/scrape")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def get_settings() -> dict:
    """Current runtime settings (API keys are returned as configured, not
    masked - this tool is for trusted local dev use, see this repo's
    CLAUDE.md on the Docker permission grant's own scope)."""
    with _client() as c:
        resp = c.get("/api/settings")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_install_cache() -> dict:
    """Every cached install's session id, game, state and size on disk."""
    with _client() as c:
        resp = c.get("/api/games/install/cache")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def clear_install_cache(session_id: int | None = None) -> dict:
    """Clear one cache by session id, or every non-running cache if omitted."""
    with _client() as c:
        path = f"/api/games/install/cache/{session_id}" if session_id is not None else "/api/games/install/cache"
        resp = c.delete(path)
        resp.raise_for_status()
        return resp.json() if resp.content else {"cleared": session_id}


@mcp.tool()
def list_proton_builds() -> dict:
    """Every Proton/Wine build the server knows about (installed + downloadable)."""
    with _client() as c:
        resp = c.get("/api/games/install/proton-builds")
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def run_tests(path: str = "tests/") -> str:
    """Run the backend pytest suite (or one file/test within it) from
    /src/backend inside the container - use this instead of re-deriving the
    pytest invocation by hand."""
    result = subprocess.run(
        ["flatpak-spawn", "--host", "--", "docker", "exec", "mog-server", "python3", "-m", "pytest", path, "-v"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout + result.stderr


@mcp.tool()
def tail_server_logs(container: str = "mog-server", lines: int = 100) -> str:
    """Tail the server container's logs via docker (host Docker daemon,
    reached through flatpak-spawn - see this repo's CLAUDE.md)."""
    result = subprocess.run(
        ["flatpak-spawn", "--host", "--", "docker", "logs", "--tail", str(lines), container],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout + result.stderr


if __name__ == "__main__":
    mcp.run()
