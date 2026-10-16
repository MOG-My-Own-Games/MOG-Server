# Backups

MOG Server backs up what only it has: its database (the users, libraries, matches, install sessions, devices, settings), the
avatars and the artwork you uploaded, and, when you ask, the saves. The install caches and the image cache are not in a
backup: they are made again.

## Where they go

Into a volume of their own mounted at `/backups` (`BACKUP_PATH`), ideally on another disk than the data volume, so a backup
survives what it protects. In the repo's `docker-compose.yml` it is `./data/backups:/backups`. Without a directory there
backups are off and Settings > Backups says so; the server starts all the same.

## Which backups

| Kind | When | What it holds |
| --- | --- | --- |
| Daily | Once a day, from `BACKUP_HOUR` (03:00 server time; made at start if the server was off then), while "Daily backup" is on in Settings > Backups | the database, avatars and uploaded artwork, and the saves if "Include the saves" is on there (off by default: they can be large) |
| Before an update | At a start that has migrations to run, before it migrates | the database alone, so it is quick |
| Manual | The "Back up now" button in Settings > Backups (`POST /api/backups`) | the same as a daily one; the saves when ticked |

The newest few of each kind are kept: `BACKUP_KEEP_DAILY` (7), `BACKUP_KEEP_PRE_UPDATE` (5), `BACKUP_KEEP_MANUAL` (10). A backup
that fails leaves nothing half-written, and the reason shows in Settings > Backups and the log. A failing backup before an
update never stops the update.

Each is one file, `mog-<kind>-<date>-<time>[-saves].tar.gz`, holding `mog.db` (a whole copy made with SQLite's own backup, so
it is correct while the server writes), `manifest.json` (the kind, the date, the version, the database revision),
`resources/avatars`, `resources/media` (without `found/` in each game's folder: the icons read from the game's files, which are read again when needed) and, with saves, `saves/`.

## Restoring

There is no button for it, on purpose: a restore replaces what is running.

1. Stop the server (`docker compose stop mog-server`).
2. Unpack the backup over the data volume (the one mounted at `/mog`): `mog.db` is the database, and the folders go to
   `resources/` and `saves/`. Keep the current `mog.db` aside first.
3. Start the server. It migrates the database to the version it runs, if the backup is older.

## API

Administrators only, and not needed by a client: `GET /api/backups` (the volume, the switches, what is kept, the backups),
`POST /api/backups?saves=true|false` (a manual backup now; `409` while another runs, `503` with no volume),
`GET /api/backups/{name}/download` and `DELETE /api/backups/{name}`.
