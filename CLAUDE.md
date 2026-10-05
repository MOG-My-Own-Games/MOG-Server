# MOG Server - Agent Guide

Self-hosted scan + scrape + sandboxed install for a personal PC game library.
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games). Stack:
Python (FastAPI, SQLAlchemy 2.0, Alembic, SQLite) backend; a plain,
no-build-step HTML/CSS/JS frontend served by the same process (see
`frontend/` and this file's own note on it below).

Read `CLAUDE.local.md` (gitignored, not committed) before writing any
repo-wide text (docs, comments, commit messages, PR descriptions) - it
covers branding and naming rules that stay out of version control on
purpose.

## Never commit or push without being directly asked

Standing rule, independent of the rules above: do not run `git
commit`, `git push`, or open a PR unless the user explicitly asks for that
specific action in that turn. Implementing and testing a change is not
itself a request to commit it.

## Docker permission

You may use `flatpak-spawn --host -- docker ...` (build/up/down/logs/exec)
for this repo's own container freely, without asking each time - this
repo's dev loop depends on it (the install sandbox can't be meaningfully
tested outside a real container: it needs bubblewrap, Xvfb, Wine/Proton).
This grant is scoped to the `mog-server` container defined in this repo's
`docker-compose.yml`; it is not a general grant to touch unrelated
containers or hosts.

## Directory map

```
backend/
  main.py, startup.py        FastAPI app, migrations-on-boot, first-run admin
  config/                    Env-var loading (bootstrap-only fallback, see below)
  models/                    SQLAlchemy models: user, library, game, install_session, settings, notification, device, save_version
  handler/
    auth.py                  HTTP Basic against the users table (see docs/TODO.md)
    database/                Per-entity CRUD handlers (db_*_handler), engine/session
    filesystem/              Path resolution + installer_detection.py (pure ranking logic)
    metadata/                igdb_handler.py, sgdb_handler.py (search-and-apply, not auto-match)
    scan_handler.py           One Game per top-level library entry
    scrape_handler.py        Best-effort auto-match: top search result, applied as-is
    install/                 The sandbox: runner.py (orchestration), sandbox.py (bwrap),
                              vnc.py (incl. the vnc_token capability-URL scheme), manifest.py
                              (hashing/live-manifest), proton_builds.py, archive_prescan.py,
                              windows_output.py, auto_mode/ (OCR-driven auto-advance)
  endpoints/                 FastAPI routers: libraries.py, games.py, install.py, settings.py
  alembic/                   Migrations (single SQLite-only chain, starts at 0001)
  tests/                     pytest, mirrors backend/ layout - pure-logic modules
                              (installer_detection, auto_mode/engine, scrape_handler) first
frontend/                    Plain HTML/CSS/JS, no build step - index.html, app.js, style.css.
                              Hash-routed (#/, #/game/<id>, #/settings) single-page shell;
                              served by FastAPI's StaticFiles mount in main.py. See
                              docs/TODO.md for the planned Steam-like redesign.
docker/                      Dockerfile (single image: API + sandbox), icewm-preferences
```

**Endpoint -> handler -> (database | filesystem | metadata | install) -> models.**
Endpoints stay thin: validate, call a handler, serialize via a response schema
in `endpoints/responses/`.

## Architecture decisions

- **One container, one process.** `handler/install/runner.py`'s
  `enqueue_install` submits directly to a thread pool, and `cancel_install`
  kills the sandboxed process group directly - no cross-process job queue.
  Don't reintroduce one without a reason; the whole point of the
  single-container design is not needing one.
- **SQLite, not MariaDB/Postgres.** `config.SQLITE_PATH`, one file, no
  separate DB container. Keep `backend/utils/database.py` and migrations
  SQLite-only; don't add dialect-branching for engines MOG doesn't support
  unless that actually changes.
- **No platform/console concept.** A `Library` is just a folder; a `Game` is
  just a top-level entry in it. Don't reintroduce a platform concept or
  per-platform logic - if something seems to need "what kind of game is
  this", it probably doesn't (the install sandbox already only targets
  Windows installers either way).
- **Settings are DB-backed, env vars are only the bootstrap fallback.** The
  `settings` table (one row, `models/settings.py`) holds what genuinely
  needs changing from the web UI without shell access: the IGDB/SteamGridDB
  keys, the default install cache TTL, the default Proton build. Each has a
  small `default_*`/`_credentials` resolver (e.g.
  `handler/install/proton_builds.py`'s `default_build_id`) that checks the
  DB row first and falls back to the matching env var - follow that pattern
  for a new one rather than reading the env var directly. Everything else
  genuinely stays env-vars-only (no full RomM-style live-reloadable YAML
  config) - don't grow this into one without a concrete need.
- **The VNC view is embedded via a capability token, not Basic auth.**
  `models/install_session.py`'s `vnc_token` (random per session) is baked
  into `vnc_url` itself; the proxy routes in `endpoints/install.py` check it
  instead of the usual `CurrentUser` dependency. This exists because a
  browser's native Basic-Auth prompt never fires for an `<iframe>`'s own
  request (only for a top-level navigation), so Basic auth alone left the
  embedded installer view permanently unauthenticated. Don't add
  `user: CurrentUser` back to `install_vnc_http`/`install_vnc_ws`.

## Conventions

- **Naming:** Classes `PascalCase`; functions/vars `snake_case`; constants
  `UPPER_SNAKE_CASE`.
- **DB sessions:** decorate handler methods with `@begin_session`
  (`decorators/database.py`); it injects and manages the session. Don't open
  sessions ad hoc.
- **Keep comments short, focused on why, not what.** A comment earns its
  place by explaining a non-obvious constraint or a bug it's guarding
  against, not by restating the line below it.
- **Tests travel with code.** New logic gets a test in `backend/tests/`,
  mirroring the module it covers (`handler/foo.py` ->
  `tests/handler/test_foo.py`). Prioritize pure-logic modules (no DB/network/
  filesystem) - they're the cheapest to test correctly and the ones most
  worth protecting with a regression test when a bug is found there (see
  `tests/handler/install/auto_mode/test_engine.py`'s own progress-screen
  test for the shape this should take: name it after the bug, explain what
  broke in the docstring). Mock the handler layer for anything that calls
  out to it (`tests/handler/test_scrape_handler.py`). Run with
  `uv run pytest tests/` or `python3 -m pytest tests/` from `backend/` -
  there's no DB fixture/conftest yet, so a test that needs real persistence
  needs to set one up itself for now.

## Commands

```bash
cd backend
pip install -e ..[dev]
alembic revision -m "short description"   # new migration (hand-write the up/downgrade)
alembic upgrade head
python3 main.py
pytest tests/
```

Docker (local build): `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build` (see the Docker
permission note above for using `flatpak-spawn` to run these yourself).
