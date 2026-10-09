# Integrating MOG Server into another client

This document is for developers of game managers, launchers, frontends and other tools who want to use a **MOG
Server as an installation provider**: your software keeps the library and the user experience, and MOG does the
hard part of getting a game that came as an installer onto the machine.

It describes what the server offers, the exact order of calls that installs a game, what our own client
([MOG Client](https://github.com/MOG-My-Own-Games/MOG-Client)) does around it, and the places where a first
implementation usually goes wrong. The server's own OpenAPI description is the reference for every field
(`GET /openapi.json`, with an interactive page at `/docs`); this guide is the story that ties the endpoints
together.

- [1. What MOG is, and what it is not](#1-what-mog-is-and-what-it-is-not)
- [2. Concepts](#2-concepts)
- [3. Talking to the server](#3-talking-to-the-server)
- [4. Browsing the library](#4-browsing-the-library)
- [5. Installing a game](#5-installing-a-game)
- [6. Downloading the result](#6-downloading-the-result)
- [7. After the download: what the client owns](#7-after-the-download-what-the-client-owns)
- [8. Saves (optional)](#8-saves-optional)
- [9. Mods (optional)](#9-mods-optional)
- [10. Notifications and other endpoints](#10-notifications-and-other-endpoints)
- [11. Deep links from the web UI](#11-deep-links-from-the-web-ui)
- [12. What MOG Client does, by module](#12-what-mog-client-does-by-module)
- [13. A minimal integration, step by step](#13-a-minimal-integration-step-by-step)
- [14. Example: install one game in about 80 lines](#14-example-install-one-game-in-about-80-lines)
- [15. Security and compatibility notes](#15-security-and-compatibility-notes)

## 1. What MOG is, and what it is not

You point a MOG Server at folders of installers you own. It scans them, matches each game with metadata (IGDB,
SteamGridDB, HowLongToBeat), and, when a client asks, **runs the installer in a sandbox on the server** (bubblewrap,
a virtual display, Wine or Proton) and **streams the files the installer wrote to the client while it is still
running**. The user clicks Install, waits, and plays: nobody drives the original installer.

MOG is not a game downloader, a store, a DRM bypass, a launcher or a streaming service (it streams installation
data, never gameplay). Your client plays the part of the launcher. If a game comes as an archive with the game
already inside, or as a folder of files, the server can also hand those over as they are (see
[5.3](#53-deciding-what-to-run)).

## 2. Concepts

| Term | Meaning |
| --- | --- |
| **Library** | A folder on the server that is scanned. Every top-level file or folder in it is one game. Users can be kept away from some libraries (`hidden_library_ids`). |
| **Game** | One top-level entry of a library, with its metadata. Several games that matched the same title (`igdb_id`) are versions of it. |
| **Installer candidate** | A file the server found that may install the game (a setup program, an archive, a disc image, a Linux installer script). |
| **Install session** | One attempt to install a game for one user. It has a state, a progress, and, when the installer is done, a list of files. |
| **Install cache** | The folder on the server where a session's output lives until it expires or is deleted. Your client downloads from it. |
| **Manifest** | The list of the cache's files with sizes, and, once the install is over, a SHA-1 for each. |
| **Device** | A machine a user runs a client on. Only needed for save sync. |

Everything is per user: one user's sessions, saves and notifications are not another's.

## 3. Talking to the server

**Base URL.** The API lives under `/api` on the server's address, for example `http://192.168.1.42:5000/api/...`.
The docker image listens on port 5000 inside; the mapped port is up to whoever runs it.

**Authentication.** HTTP Basic, on every request, against the server's own users. There is no token and no
session: send the `Authorization` header each time. A wrong or disabled account gets `401` with
`WWW-Authenticate: Basic`. The server remembers a verified password for a few minutes, so sending it every time
is cheap.

```bash
curl -u admin:secret http://localhost:5000/api/users/me
# {"id":1,"username":"admin","enabled":true,"role":"admin","avatar_path":null,"hidden_library_ids":[]}
```

Two roles exist, `admin` and `user`. What a client needs for installing and playing works for both. Settings,
creating libraries, scanning and scraping are admin only.

`GET /api/health` needs no login and answers `{"status": "ok", "version": "..."}`. Use it to tell "no server here"
from "wrong password", and to read the server's version.

**Errors.** Failures are JSON with a `detail`: usually a string, an object for the few cases that need to say more
(the `409` of device registration, see [8](#8-saves-optional)). The statuses you will meet are `401` (login),
`404` (no such game, session, file, or an endpoint an older server does not have), `409` (a conflict you can
resolve), `416` (a range is not available yet, see [6](#6-downloading-the-result)), `422` and `413` (a rejected
upload) and `429` (too many installs running at once).

**Newer and older servers.** Optional features appear over time. A client should treat a `404` or `405` on an
optional endpoint (game sizes, the library revision, mods, save sync, save paths, download workers) as "this
server does not have that" and carry on. MOG Client does exactly this everywhere: asking for more is never a
reason to stop the install.

**Redirects and proxies.** A reverse proxy in front of the server is fine, but the client must be given the final
URL: the streaming calls do not follow a redirect to another origin.

## 4. Browsing the library

| Call | What it gives |
| --- | --- |
| `GET /api/libraries` | The libraries the user can see. |
| `GET /api/games` | Every game the user can see, with metadata. `?library_id=` limits it to one library. |
| `GET /api/games/{id}` | One game. |
| `GET /api/games/revision` | One short string that changes when the library does. Poll it (MOG Client does, every 20 seconds) and reload the list only when it differs from the last one you saw. |

A game object carries, among others:

- `id`, `name` (the display name, matched or edited), `fs_name` (the folder or file name on disk), `library_id`;
- `summary`, `igdb_id`, `igdb_metadata` (the whole IGDB record: genres, release date, developers, publishers,
  modes, screenshots, age ratings, DLC and so on), `sgdb_id`;
- `hltb_id` and `hltb_metadata`: how long the game takes, in **seconds**: `main_story`, `main_plus_extra`,
  `completionist` and `all_styles` (any of them may be missing), each with a `_count` of reports. Show nothing
  when it is empty: a game that has not been matched simply has no times;
- `cover_path` and `media`: the artwork the server chose for each kind (`cover`, `banner`, `hero`, `logo`,
  `icon`);
- `size_bytes`: what the game's own folder (or file) takes on the server's disk, `null` until the server has measured it
  (a scan does, and `GET /api/games/{id}/size` refreshes it). Sort by it with `null` last;
- `installed` (this user has a finished install whose cache is still on the server), `last_played` and
  `last_played_on` (from the user's save uploads), `missing_from_fs`, `saves_only`, `addons_only`, `fs_tags`.

Artwork is served by the server so a client does not depend on the metadata providers' CDNs:
`GET /api/games/{id}/cover`, `GET /api/games/{id}/media/{kind}`, `GET /api/games/{id}/screenshots/{index}` and
`GET /api/games/{id}/videos`. Ask for what the game object says exists and fall back gracefully when it does not.

Other useful reads: `GET /api/games/{id}/size` and `GET /api/games/{id}/sizes` (what the game, its install
caches and its saves take on the server), and `GET /api/games/{id}/files` (the game's own files in the library,
not the install).

**Grouping.** MOG Client shows games that share an `igdb_id` as one entry with "N versions". When the user
installs it, the installers of every version are listed, each under its version's name, and the user picks one.
Your client can do the same or show each game on its own.

## 5. Installing a game

### 5.1 The whole flow

```text
GET  /api/games/{id}/install            -> is there already a finished install to download? (404 = none)
GET  /api/games/{id}/install/candidates -> what would be run? (optional, for deciding with the user)
POST /api/games/{id}/install            -> start (or resume) the session, get its id
loop every ~3 s:
  GET /api/games/{id}/install?session_id=ID                -> state, progress, errors
  GET /api/games/{id}/install/stream/manifest?session_id=ID -> the files so far
  GET /api/games/{id}/install/stream/{path}?session_id=ID   -> Range requests, as the files are written
state == done and every file downloaded
GET  /api/games/{id}/install/files?session_id=ID            -> final list with SHA-1, verify, repair
```

The server starts producing files while the installer is still running, so your download can run **alongside**
the install instead of after it. MOG Client does this: its progress bar is about the download, and "the server is
done" is only one of the two conditions for finishing (see [6.5](#65-when-is-it-finished)).

### 5.2 Starting a session

`POST /api/games/{id}/install` with a JSON body (every field optional):

| Field | Meaning |
| --- | --- |
| `installer_path` | A file to run, as a path relative to the game, taken from the candidates. Omit it to let the server pick. |
| `source_path` | An archive or disc image of the game's own to unpack and look into, with `installer_path` naming the executable inside it. |
| `extract_only` | `true`: do not run anything, take the contents as they are (an archive unpacked, or the game's own files) as the install. `false`: run an installer even if the server would have extracted. Left out: the server decides. |
| `auto_mode` | Let the server drive the installer's windows by itself (OCR driven). Left out: the server's default (`GET /api/games/install/defaults`). |
| `manual_mode` | Do not pick an installer; wait for a person to ([5.4](#54-a-person-has-to-choose)). |
| `proton_build` | Which Proton build to use; left out, the server's default. |
| `ttl_seconds` | How long the install cache lives; left out, the server's default (7 days unless an admin changed it in Settings; a default of `0` means never); `0` or less means never. An admin who sets a default of days gives every cache that never expires that expiry, counted from then; `POST /api/games/install/cache/{session_id}/reset` (admin) starts one cache's expiry over from the default in force at that moment. The admin cache listing, `GET /api/games/install/cache`, carries each cache's `expires_at` (`null`: never). |

The call is **idempotent while it runs**: if a session for the game is already `installing` or `streaming`, it is
returned as it is, so a second client asking for the same game finds the first one's work.

> **Check for a finished install before you start another.** A start for a game whose last session is already
> `done` would run the installer again. MOG Client first does `GET /api/games/{id}/install`: when the answer is
> `done`, it downloads from that session and does not start anything. A `404` means there is no session.

A start can fail with `429` when the server is already running as many installs as it was told to
(`INSTALL_MAX_CONCURRENCY`, one by default). Tell the user and offer to try again later.

The response is the **session**:

```json
{
  "id": 17, "game_id": 83, "user_id": 1, "state": "installing",
  "installer_path": "setup_game.exe", "source_path": null,
  "phase": null, "phase_detail": null,
  "auto_mode": true, "manual_mode": false, "extract_only": false,
  "auto_status": "running", "auto_detail": "clicking Next",
  "vnc_url": "/api/games/install/vnc/6901/vnc.html?...", "cache_path": "/cache/17",
  "bytes_written": 0, "bytes_total": 0, "error": null
}
```

Pass `session_id` on every later call that reads or downloads, so your client stays on the attempt it started even
if a newer session for the same game appears meanwhile.

### 5.3 Deciding what to run

You can let the server decide, or look first with
`GET /api/games/{id}/install/candidates` (and `?source=<archive>` to look inside one of its archives, without
unpacking anything). A candidate has a `path`, `file_name`, `file_size_bytes`, a `kind` (`known installer`,
`executable (top level)`, `executable (nested)`, `disc image`, `archive`, `linux installer`) and a `category`
(`game`, or an add-on: `dlc`, `mod`, `update`, `patch`, ...; add-ons are listed after the game and never picked
by default). The answer also has `needs_manual_pick` (nothing was found) and `extract_suggested`.

**`extract_suggested`** is what makes "this is not an installer" visible. It is true when nothing in the folder (or
in the archive you asked about) looks like an installer: the server looks for known installer names
(`gog-*`, `setup*`, `install.exe`, and any name containing "setup" or "install", never an "uninstall"), archives and
disc images to unpack further, and Linux installer scripts. If there is none, the files are probably the game
itself. What MOG Client does with that:

1. **A game that is an archive with no installer inside:** ask the user "no installer was found in X: extract its
   contents and use them as they are?". Yes starts with `extract_only: true`; No starts with `extract_only: false`
   and the server runs what it finds inside as an installer, as before.
2. **A game that is a folder with no installer in it:** the server does not run any executable by itself. The
   session waits in `awaiting_installer`, and the client shows the folder's executables together with a
   **"Just extract"** entry (start with `extract_only: true` and no file named) so the user can choose.
3. **Anything else:** start without asking.

A client that does not ask can simply leave `extract_only` out: an archive without an installer is then extracted
as it is and nothing is run. Send `extract_only: false` only after a person said so.

A game with several installers (a base game and a patch, say) is installed one at a time: each start names one
`installer_path`, and the next one goes into the same install cache.

### 5.4 A person has to choose

A session can end up in `awaiting_installer`: nothing was found to run, `manual_mode` was set, or the server
declined to guess (the folder case above). Nothing moves until someone decides. Two ways out:

- Let the person pick from the candidates, then `POST` again with that `installer_path` (or `extract_only`).
- Hand them the installer's screen: when a display is up, `vnc_url` is a path on the server (`base + vnc_url`)
  that opens a noVNC page in a browser. It carries its own token, so an embedded web view needs no login. They
  can click through the installer themselves, and your client keeps downloading what it writes.

MOG Client treats an `awaiting_installer` that comes back from a start as "needs a pick": it opens its own list of
installers (without "let the server choose", because the server just said it cannot) rather than sending the user
to a web page.

### 5.5 Auto mode

With `auto_mode` on, the server clicks through the installer by itself. The session's `auto_status` says how it
goes (`running` with an `auto_detail` such as the button it is pressing, or `needs_manual` when it is stuck). When
it needs a person, the server also puts a notification in the user's inbox ([10](#10-notifications-and-other-endpoints));
tell the user and offer `vnc_url`. `PATCH /api/games/{id}/install/auto-mode` with `{"enabled": false}` switches it
off for a running session.

### 5.6 Session states

| `state` | What it means | What your client does |
| --- | --- | --- |
| `detecting` | The server is looking at the files. | Keep polling. |
| `awaiting_installer` | Waiting for a decision ([5.4](#54-a-person-has-to-choose)). | Ask the user. Stop waiting for files. |
| `installing` | The installer (or the extraction) is running. `phase` and `phase_detail` say what: `extracting`, `mounting`, `downloading` (a Proton build), `preparing`, `launching`. | Show it; download what is already there ([6](#6-downloading-the-result)). |
| `streaming` | The installer is done and the files are being sealed. | Keep downloading. |
| `done` | Finished; the final list is available. | Finish the download, verify. |
| `failed` | Something went wrong: read `error` (a cancelled session also ends here, with `error: "Cancelled"`). | Show `error`. |
| `expired` | The cache's time ran out and was deleted. | Start again. |

`bytes_written` and `bytes_total` are the server-side progress where it knows one (unpacking, hashing the result).
They are not your download's progress: compute that from what you have on disk.

Poll about every **3 seconds**. Cancel with `POST /api/games/{id}/install/cancel` (the session fails with
"Cancelled" and its cache is cleared), and forget a finished one with `DELETE /api/games/{id}/install`.
`GET /api/games/install/active` lists the user's running installs, for a dashboard.

## 6. Downloading the result

### 6.1 The manifest

`GET /api/games/{id}/install/stream/manifest?session_id=ID` answers **whether the install is still running or not**,
so you poll one endpoint throughout:

```json
{ "game_id": 83, "files": [
  { "path": "Game/Game.exe", "size_bytes": 7930368,  "sealed_bytes": 7930368, "complete": true  },
  { "path": "Game/data.pak", "size_bytes": 912340992, "sealed_bytes": 402653184, "complete": false }
] }
```

While the installer runs, `size_bytes` is what the file has now and may grow, `sealed_bytes` is the prefix that is
safe to read (it will not change any more), and `complete` is false. After the install is done every file is
complete and `sealed_bytes == size_bytes`. A `404` just means the list does not exist yet: wait.

Paths use `/` and are relative to the install folder. Percent-encode each segment when you put one in a URL.
**The list can change between the live view and the final one**: an installer may write a file in one place and
move it later. When the server is done and the list is final, drop any local file that is no longer in it (MOG
Client only deletes files that its own download created).

### 6.2 Fetching a file

`GET /api/games/{id}/install/stream/{path}?session_id=ID`, with `Range: bytes=N-` where `N` is how much you
already have of it:

- `206` with `Content-Range: bytes start-end/*`: the bytes you asked for, up to what is sealed. Append them. The
  total is not known in the header: it is the manifest's.
- `416` with `Retry-After: 1`: there is nothing new to give you yet (the file is still being written beyond what
  you have). It is not an error. Ask again later, which a loop that re-reads the manifest does anyway.
- `200`: the server ignored the range (or the file is complete and was served whole). If you asked from `N > 0`,
  **start the file over**.
- `404`: the file is not in the list (any more). Re-read the manifest.

Once a file is complete this behaves like a plain file download and ranges work as usual.

Write to disk **as the body arrives**, not at the end. A dropped connection then costs nothing: the next request
continues from the size of the file on disk. This is also how a download survives the client closing and opening
again.

### 6.3 Doing it fast and robustly

- **Parallel files.** `GET /api/games/install/defaults` has `download_workers`: how many files to fetch at once,
  as the server's owner configured it. Respect it; use one when it is missing. The server may also cap the total
  bandwidth of streaming downloads.
- **Timeouts.** Use a read timeout per socket read (MOG Client: 30 s), not for the whole transfer: a file can take
  an hour.
- **Stale connections.** When the install ends, connections that sat idle while it ran may be dead. MOG Client
  replaces them one by one when it sees the server is done, and retries a file with a growing back-off (up to 30
  s) when it fails, without losing what is on disk.
- **Do not hammer the manifest.** Re-read it about every 3 seconds, and sooner (every half second) only while
  data is flowing.

### 6.4 Verify and repair

When the session is `done`, `GET /api/games/{id}/install/files?session_id=ID` is the final list with a **SHA-1**
for every file:

```json
{ "game_id": 83, "files": [ { "path": "Game/Game.exe", "size_bytes": 7930368, "sha1": "ab12..." } ] }
```

Hash what you downloaded. For a mismatch, fetch the file whole with
`GET /api/games/{id}/install/files/{path}?session_id=ID` and check again. Files streamed while the installer was
still working can differ from the final ones in rare cases, which is why this step exists. Keep the list: MOG
Client stores it as "the game's own files", so later it can tell a game's files from the save files the game
writes.

`GET /api/games/{id}/install/download` gives the whole result as one ZIP, built on demand. It is handy for
scripts, but it cannot be resumed, so a client should prefer the per-file route.

### 6.5 When is it finished?

Only when **both** are true: the session is `done`, and every file of the final list is on disk with the right
size. The server being done is not enough (files may still be in flight), and your download having caught up is
not enough (the installer may still be writing).

## 7. After the download: what the client owns

Up to here MOG has turned an installer into a folder of files. From here on it is yours. MOG Client, for
reference, does this:

1. **Where.** The user chooses install folders in Settings. A new game goes into the first folder that is
   connected and has room for the size reported by `GET /api/games/{id}/size`; when an earlier folder is full the
   user is asked before a later one is used. The game gets its own folder named after the game (illegal
   characters replaced).
2. **Resume.** The install is recorded as `installing` before the first byte arrives. If the client is closed or
   crashes, the record and the files on disk are what resume uses (the next request is a `Range` from the file
   sizes). A game that was `extract_only` remembers it, so a restart does not ask again. Installs and mod downloads
   that were running when the client closed start again at the next start.
3. **Executable.** The installer's output is a tree of files, and the client has to find the program to run. MOG
   Client lists the executables it finds, the game's own start script first, then the `.exe` files biggest first,
   leaving out redistributables, uninstallers, crash handlers and helpers, and asks the user to pick one (with a
   Browse option). The install stays in the "awaiting executable" state until they do.
4. **Launch.** On Windows it runs the executable. On Linux it uses the first of Faugus, umu, Proton or Wine that is
   installed (the user can choose per game), with a Wine prefix in the game's own folder. A `.sh` start script
   (what GOG's Linux games use) runs as it is, as a **native** game, with no prefix.
5. **Entries.** It writes a small launch script in the game's folder, a desktop entry, and optionally a Steam
   shortcut with the artwork the server chose, so the game starts without the client. Existing entries are edited
   in place, never recreated.
6. **Uninstall.** Deletes the game's folder (asking before the prefix, which may hold saves) and, on request, also
   deletes the install cache on the server with `DELETE /api/games/{id}/install`.

None of this is required of you. A client that only wants to hand the files to another program needs steps 1 and
2.

## 8. Saves (optional)

The server can keep a user's save games so they follow the person from one machine to another. It stores
**opaque zip archives** per user, game and device, and keeps the newest few of each (the response says how many,
`keep_versions`, three by default).

### 8.1 Devices

A device is a machine. Register it once and keep the answer:

```http
POST /api/devices/register
{ "client_uid": "<a random id your client keeps>", "hostname": "karasu", "platform": "linux", "os_id": "fedora" }
```

The answer is the device (`id`, `name`, ...). Send the same `client_uid` later and you get the same device back.
Two cases need the user:

- `409` with `detail.code == "hostname_taken"`: another of the user's devices already uses this hostname. The
  detail lists those `devices` and some `suggested_names`. Ask whether this is one of them (register again with
  `adopt_device_id`) or a new machine (register again with a `name` of its own).
- `409` with `detail.code == "name_taken"`: the name you asked for is used.

`GET /api/devices` lists them (each with `log_at`, when it last sent its log, or `null`); `PATCH /api/devices/{id}` with `{"name": "..."}` renames one.

**Device log (optional, opt-in).** A client whose user allowed it can send its own log, so a problem on a machine out of
reach can be read: `PUT /api/devices/{id}/log` with the log as a plain-text body (`Content-Type: text/plain`; `204`).
It replaces the one sent before, and only the newest 1 MiB is kept; a body over twice that is refused with `413`. It
is a request of its own, not part of a save archive, and it is stored apart from the saves. `GET /api/devices/{id}/log`
returns it as plain text (`404` when none was sent). Only the device's own user can use either. An older server
answers `404`: treat that as "not supported" and carry on, the log is never a reason to fail a save sync.

### 8.2 Versions

| Call | What it does |
| --- | --- |
| `POST /api/games/{id}/saves?device_id=D&trigger=T` | Upload a zip (multipart form field `file`) as the newest version of device `D`. `T` is `launch`, `quit`, `manual`, `uninstall` or `sync`: a label shown next to the version. The answer has `created: false` when the archive is identical to the device's newest, and nothing was stored. |
| `GET /api/games/{id}/saves` | Every device's versions, newest first, with `keep_versions`. |
| `GET /api/saves/{version_id}` | One version with a (capped) list of the files in it. |
| `GET /api/saves/{version_id}/download` | The zip. |
| `DELETE /api/saves/{version_id}` | Remove one. |

An archive that is too large is refused with `413` (default cap 512 MiB), and one that is not a valid zip, or has
unsafe member names, with `422`.

### 8.3 What the archive holds

The server does not look inside beyond checking that it is safe, so the layout is a contract between clients.
MOG Client's, which another client must follow to read its archives, is: each file is stored under a **key** that
says where it came from, with `/` separators.

- `game/<path>`: a file inside the game's install folder (relative path).
- `users/USER/<Documents | Saved Games | AppData/Roaming | AppData/Local | AppData/LocalLow>/<path>`: a file in
  the Windows profile of the game's Wine prefix.
- `ProgramData/<path>` and `users/Public/<path>`: shared Windows folders in the prefix.
- A native game uses keys that name the folder the files came from.

Keys are checked on the way in and on the way out: a key that tries to leave its folder refuses the whole archive.
`GET /api/games/{id}/save-paths` answers where a game keeps its saves on Linux when the community Ludusavi
manifest knows (`placeholders` such as `<xdgConfig>`, `<home>` and `<base>` are left in for the client to fill).

### 8.4 The policy MOG Client follows

- When the client starts, and when a game is launched, it looks at the newest version from **another** device. If
  nothing changed here, it takes that version, after copying every file it is about to replace into a backup
  archive. If both sides changed, it does neither and tells the user ("conflict"): each device keeps its own
  history, so nothing is lost.
- When a game closes, it uploads what changed (`trigger=quit`).
- It remembers which version it has already applied or declined, so it never asks twice.
- A restore never replaces a file without a backup first.

## 9. Mods (optional)

A game folder on the server can have a `mods` folder: each top-level folder or archive in it is one mod.

| Call | What it does |
| --- | --- |
| `GET /api/games/{id}/mods` | `[{name, kind, size_bytes, file_count}]`, `kind` being `folder`, `archive` or `file`. |
| `POST /api/games/{id}/mods/{name}/prepare` | A folder starts being zipped on the server; an archive or file is ready at once. |
| `GET /api/games/{id}/mods/{name}/status` | `{state, bytes_done, bytes_total, error}`, `state` being `idle`, `zipping`, `ready` or `failed`. |
| `GET /api/games/{id}/mods/{name}/download` | The file or the zip. Supports ranges and an `ETag`, so a download can resume (`If-Range`). |
| `POST /api/games/{id}/mods/{name}/cancel` | Stop zipping. |
| `POST /api/games/{id}/mods/{name}/downloaded?machine=NAME` | Tell the server the mod is on this machine: the user gets a notification. |

MOG Client does not install mods: it only fetches them into the game's `mods` folder. Percent-encode the mod's name.

## 10. Notifications and other endpoints

`GET /api/notifications` returns `{notifications: [...], unread: N}`. A notification has an `id`, a `kind`, a `title`,
a `body`, optionally the `game_id` and `session_id` it is about, `read` and `created_at`. The server writes them
for things a person should hear about even if the client was not looking: an installer that needs a person, a
finished install, a mod that is zipped or downloaded, a save that was synced. `POST /api/notifications/read` and
`POST /api/notifications/{id}/read` mark them read; `DELETE /api/notifications` and `DELETE /api/notifications/{id}`
remove them. MOG Client polls every 15 seconds and shows the unread count on the user's picture.

Admin-only endpoints (libraries, scanning and scraping, settings, users, the install cache and Proton builds) exist
for the web UI and are not needed by a client.

## 11. Deep links from the web UI

The web UI has an **Install** button that opens `mog://install/<game id>?server=<url-encoded server address>`. A
client that registers the `mog` scheme gets the game and the server and can start its install. MOG Client does this
(it checks that the server in the link is the one the user is signed in to, and asks otherwise), and the web UI
tells the user to install the client when nothing answers. If your client registers the same scheme only one of
the two will win on a machine; use a different scheme if both should work side by side.

## 12. What MOG Client does, by module

If you want to see a working implementation, these are the files to read in
[MOG-Client](https://github.com/MOG-My-Own-Games/MOG-Client) (`mog_client/`):

| Module | What it is |
| --- | --- |
| `api.py` | The HTTP client: Basic auth, retries, Range streaming of one file, and one method for each endpoint above. |
| `transfer.py` | The download loop (manifest polling, parallel workers, stall detection, vanished files), verify-and-repair, and the session poll. |
| `manager.py` | The whole install: start or resume, download, verify, record, then entries (executable, launch script, desktop entry, Steam shortcut). |
| `mods.py` | Fetching a mod (prepare, follow the zipping, resumable download, cancel). |
| `saves/` | Devices, finding a game's save files (Wine prefix or native folders), the archive format, backup and restore with the policy of [8.4](#84-the-policy-mog-client-follows). |
| `launcher.py`, `gameplay.py`, `steam.py` | Executable discovery, launch commands per engine, watching the running game, Steam shortcuts. |
| `protocol.py` | The `mog://` link and registering it with the system. |
| `grouping.py`, `ordering.py`, `snapshot.py` | Games grouped by title, the library's sort orders, and what is kept on disk so the window opens at once. |
| `cli.py` | The same flow with no window: `mog --base URL --user U --pass P --game-id N --out DIR` starts or resumes an install and streams it into `DIR`. |

The CLI is the shortest path to understanding the protocol: it is the flow above, in a few hundred lines of
standard-library Python.

## 13. A minimal integration, step by step

1. **Sign in.** `GET /api/users/me` with Basic auth. Keep the credentials safe on your side.
2. **List the games.** `GET /api/games`. Show `name`, the cover from `/api/games/{id}/cover`, the summary.
3. **Check for an install that is already there.** `GET /api/games/{id}/install`.
4. **Start one.** `POST /api/games/{id}/install` with `{}`. Handle `429`.
5. **Poll** the session and the manifest every 3 s. Stop on `failed` or `expired`; ask the user on
   `awaiting_installer`.
6. **Download** every file with Range, as the manifest grows, writing as the body arrives.
7. **Finish** when the session is `done` and every file is complete on disk.
8. **Verify** with `/install/files`, repair mismatches.
9. **Hand the folder to your launcher.** Choosing and launching the executable is your side.
10. **Optional, in this order of value:** the extract-as-it-is question ([5.3](#53-deciding-what-to-run)), resume after
    a restart, saves, mods, notifications, the `mog://` scheme.

## 14. Example: install one game in about 80 lines

Standard library only. It follows 3 to 8 above: it reuses a finished install, starts one otherwise, downloads
while the installer runs, and verifies at the end. It does not handle `awaiting_installer` beyond telling you what
to do, and it asks nothing about extracting.

```python
import base64, hashlib, json, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

BASE, USER, PASSWORD = "http://localhost:5000", "admin", "secret"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()


def call(method, path, body=None, extra=None):
    """-> (status, body bytes, headers). Never raises on an HTTP status."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": AUTH, "Accept": "application/json", **({"Content-Type": "application/json"} if data else {}), **(extra or {})}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def get_json(path):
    status, body, _ = call("GET", path)
    return status, (json.loads(body) if body else None)


def fetch_more(game, sid, rel, dest):
    """Ask for what is sealed beyond what is already in `dest`."""
    have = dest.stat().st_size if dest.exists() else 0
    url = f"/api/games/{game}/install/stream/{urllib.parse.quote(rel)}?session_id={sid}"
    status, body, _ = call("GET", url, extra={"Range": f"bytes={have}-"} if have else None)
    if status == 416:  # nothing new sealed yet: not an error
        return
    if status not in (200, 206):
        raise RuntimeError(f"{rel}: HTTP {status}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if status == 200 else "ab"  # 200: the server sent it whole, start over
    with open(dest, mode) as f:
        f.write(body)


def install(game, out: Path):
    status, session = get_json(f"/api/games/{game}/install")
    if status != 200 or session["state"] != "done":  # a finished install is reused, never run twice
        status, body, _ = call("POST", f"/api/games/{game}/install", {})
        if status != 200:
            sys.exit(f"could not start: HTTP {status} {body.decode()}")
        session = json.loads(body)
    sid = session["id"]
    while True:
        _, session = get_json(f"/api/games/{game}/install?session_id={sid}")
        state = session["state"]
        if state in ("failed", "expired"):
            sys.exit(f"install {state}: {session['error']}")
        if state == "awaiting_installer":
            sys.exit(f"pick an installer: GET /install/candidates, then POST again (or open {BASE}{session['vnc_url'] or ''})")
        status, manifest = get_json(f"/api/games/{game}/install/stream/manifest?session_id={sid}")
        files = manifest["files"] if status == 200 else []
        for f in files:
            dest = out / f["path"]
            have = dest.stat().st_size if dest.exists() else 0
            if f["complete"] and have >= f["size_bytes"]:
                continue
            if f["complete"] or have < f["sealed_bytes"]:
                fetch_more(game, sid, f["path"], dest)
        everything = bool(files) and all(f["complete"] and (out / f["path"]).exists() and (out / f["path"]).stat().st_size >= f["size_bytes"] for f in files)
        if state == "done" and everything:  # both: the server is done AND every file is down
            break
        time.sleep(3)
    _, final = get_json(f"/api/games/{game}/install/files?session_id={sid}")
    for f in final["files"]:
        path = out / f["path"]
        if hashlib.sha1(path.read_bytes()).hexdigest() != f["sha1"]:  # fine for small files; hash in chunks for big ones
            print("repairing", f["path"])
            path.write_bytes(call("GET", f"/api/games/{game}/install/files/{urllib.parse.quote(f['path'])}?session_id={sid}")[1])
    print(f"done: {len(final['files'])} files in {out}")


if __name__ == "__main__":
    install(int(sys.argv[1]), Path(sys.argv[2]))
```

For real use, stream bodies to disk in chunks instead of reading them whole, run several files at once
(`download_workers`), delete local files the final list no longer has, and hash in chunks.

## 15. Security and compatibility notes

- **Keep it on your LAN.** MOG Server is vibe-coded and has had no security audit. It speaks plain HTTP with Basic
  auth by default, so a password crosses the network in the clear. Do not expose it to the internet; if you must
  reach it from outside, put it behind a VPN or a TLS-terminating reverse proxy you trust. The project does not
  support running it exposed.
- **Treat what the server sends as untrusted on your side too.** Paths in a manifest are meant to be relative to
  the install folder; refuse any that are not (`..`, absolute paths, drive letters) before writing a byte. MOG
  Client checks the keys of a save archive this way; do the same for manifest paths in yours.
- **Games you own.** MOG is for games and installers you are entitled to use. Please keep that in what you build
  on it.
- **Stability.** The API is used by the web UI and MOG Client and changes together with them; the endpoints in this
  document are the ones a client relies on and are kept compatible, with new ones added beside them. Check
  `/api/health` for the version and treat unknown fields as ignorable.
- **Licences.** MOG Server is AGPL-3.0-or-later and MOG Client is GPL-3.0. Talking to the server over HTTP is what
  the API is for. If you copy code from either, follow its licence; if you are unsure, ask in an issue.

Questions, corrections and integrations you have built are welcome as issues or pull requests on the
[MOG-Server](https://github.com/MOG-My-Own-Games/MOG-Server) repository.
