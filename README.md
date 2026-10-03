# MOG Server

Self-hosted scan + scrape + sandboxed install for a personal PC game library.
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games). See the
org profile for the project's scope and philosophy.

**Not for piracy.** This is for DRM-free games and games you own a legitimate
copy of.

## What it does

- Scans one or more library folders; each top-level entry becomes a Game.
- Matches metadata via IGDB and SteamGridDB - a one-click "Scrape" per game
  or per library, or search-and-apply by hand (genres, screenshots, age
  ratings, DLC/expansions/remakes all come along with a match).
- Runs an installer for a Game inside a sandboxed Wine/Proton environment
  (bubblewrap + Xvfb + VNC, viewable live in the web UI), with an
  experimental OCR-driven "auto mode" that presses the installer's own
  buttons, and a checklist to chain several installers (e.g. a patch) into
  the same install run.
- Exposes the installed files over HTTP, including while the install is
  still running ("stream install": a client can start downloading a file's
  already-written bytes before the installer has finished writing it).
- A small built-in web UI (no build step, served by the same process) to do
  all of the above by hand; API keys, the default Proton build, and the
  install cache are all manageable from its Settings page.

One container does everything: there's no separate worker process to deploy.

## Quick start

```bash
cp env.template .env   # fill in MOG_ADMIN_PASSWORD at least
docker compose up -d   # pulls xargonwan/mog-server:latest
```

To build from source instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Mount your game folders under `./data/library` (see `docker-compose.yml`; it is mounted read-only),
then open **http://localhost:5000**, sign in, and add a Library pointing at
it from Settings (or via `curl`, same effect):

```bash
curl -u admin:<password> -X POST http://localhost:5000/api/libraries \
  -H 'Content-Type: application/json' \
  -d '{"name": "My Games", "root_path": "/library/my-games"}'
curl -u admin:<password> -X POST http://localhost:5000/api/libraries/1/scan
```

From there the web UI covers scanning, scraping, and installing. A
[MOG-Client](https://github.com/MOG-My-Own-Games/MOG-Client) CLI walkthrough
covers the same from the command line (logging in, listing games, starting
an install) - useful for scripting or a headless setup.

## Development

See `CLAUDE.md` for the directory map, conventions, and standing rules for
working in this repo (including with an AI agent).

```bash
cd backend
pip install -e ..[dev]
alembic upgrade head
python3 main.py      # http://localhost:5000
pytest tests/
```

## License

AGPL-3.0-or-later. See `LICENSE` and `NOTICE.md` (this repo adapts source
from RomM, also AGPLv3 - see `NOTICE.md` for what and why).
