<p align="center"><img src="frontend/assets/mascotte.png" width="200" alt="MOG" /></p>

# MOG Server

Self-hosted scan + scrape + sandboxed install for a personal PC game library.
Part of [MOG - My Own Games](https://github.com/MOG-My-Own-Games). See the
org profile for the project's scope and philosophy.

**Not for piracy.** This is for DRM-free games and games you own a legitimate
copy of.

## What it does

- Scans one or more library folders; each top-level entry becomes a Game.
- Matches metadata via IGDB and SteamGridDB, and chooses the artwork for each game (cover, banner,
  hero, title logo, icon: SteamGridDB first, IGDB as the fallback). The server downloads and serves
  the chosen images, so clients build their Steam shortcuts from it. "Scrape" per game opens a dialog
  that applies the providers' defaults and lets you pick any other artwork; automatic scrapes keep to
  the defaults and never undo a pick.
- Metadata comes from a one-click "Scrape" per game or per library, or a search-and-apply by hand
  (genres, screenshots, age ratings, DLC/expansions/remakes all come along with a match).
- Keeps each machine's save files per game (the client uploads them) and lets them follow the
  player: a newer save from another machine is offered, or brought over, when a game starts. The
  last three versions of every device are kept, listed under the game's Files > Saves tab (so you
  can tell where a save came from), and a version can be downloaded or a zip uploaded by hand. Machines are named after their hostname, asking when a name is already
  taken (a reinstall, or a second system on the same computer). A game that is gone from disk but
  still has saves is not "missing": it stays, marked "Saves only" (amber corner with a floppy disk),
  and clearing the missing games leaves it alone; deleting it asks whether to keep the saves (the default, the game then stays listed) or delete
  both, and deleting a user asks first and says how many saved games would be lost. A folder with only mods or DLC and nothing that installs the game
  is marked "Add-ons only" (violet corner with a puzzle piece).
- Offers a game's mods for download (never installed): every top-level folder or archive inside the game's `mods` folder
  is one mod, so `mods/mod1/file.zip` and `mods/mod2.zip` are mod1 and mod2. An archive is served as it is; a folder is zipped
  on the fly in the background (`/api/games/{id}/mods`, `.../{name}/prepare`, `.../status`, `.../download`), with progress for
  the client and a notification when the zip is ready. Zips are kept in `/mog/cache/mods` and forgotten after
  `MODS_CACHE_MAX_AGE_HOURS` (24 by default).
- Runs an installer for a Game inside a sandboxed Wine/Proton environment
  (bubblewrap + Xvfb + VNC, viewable live in the web UI), with an
  experimental OCR-driven "auto mode" (on by default; if it gets stuck or the
  install fails the user gets a notification, bell icon and Notifications page
  in the web UI and in the client) that presses the installer's own
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
