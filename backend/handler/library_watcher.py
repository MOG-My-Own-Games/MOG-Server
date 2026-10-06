"""Keeps the games in step with the library folders. A game added, removed or renamed on disk (a share filled
by hand, a folder deleted) is picked up without anybody pressing Scan: the top level of each library folder is
looked at every few seconds, and when it has changed and then stayed the same for a poll (a copy in progress
keeps changing it) the library is scanned, the same scan the Scan button runs.

Only the top level is compared: each entry's name, kind, size and modification time. A folder's own time moves
when something is added or removed directly inside it, which covers a game getting an installer or a patch;
a change deeper down is for the Scan button."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from config import LIBRARY_WATCH_INTERVAL
from logger.logger import log
from models.library import Library

from handler.database import db_game_handler, db_library_handler
from handler.scan_handler import ScanResult, scan_library
from handler.scrape_handler import needs_scrape, scrape_library_in_background

Signature = tuple[tuple[str, bool, int, int], ...]


def signature(root: Path) -> Signature | None:
    """What the top level of `root` looks like, or None when it cannot be read (a share not mounted)."""
    try:
        with os.scandir(root) as entries:
            found = []
            for entry in entries:
                try:
                    info = entry.stat()
                    is_dir = entry.is_dir()
                except OSError:
                    continue  # gone between listing and looking
                found.append((entry.name, is_dir, 0 if is_dir else info.st_size, info.st_mtime_ns))
    except OSError:
        return None
    return tuple(sorted(found))


@dataclass
class _Seen:
    scanned: Signature | None = None  # what was there at the last scan
    last: Signature | None = None  # what the previous poll saw


@dataclass
class LibraryWatcher:
    libraries: Callable[[], list[Library]] = db_library_handler.get_all_libraries
    look: Callable[[Path], Signature | None] = signature
    scan: Callable[[Library], ScanResult] = scan_library
    after_scan: Callable[[Library], None] = lambda library: _scrape_what_is_missing(library)
    seen: dict[int, _Seen] = field(default_factory=dict)

    def poll(self) -> list[int]:
        """One look at every library; the ids of those scanned because of it.

        A library is scanned when it differs from what was last scanned and has looked the same on two polls
        in a row. One seen for the first time (after a start) differs from nothing, so it is scanned once,
        which also catches what changed while the server was down."""
        libraries = self.libraries()
        for gone in set(self.seen) - {library.id for library in libraries}:
            del self.seen[gone]
        scanned = []
        for library in libraries:
            now = self.look(Path(library.root_path))
            if now is None:
                continue  # not readable now: a share that is away must not turn every game "missing"
            state = self.seen.setdefault(library.id, _Seen())
            settled = now == state.last
            state.last = now
            if now == state.scanned or not settled:
                continue
            if self._scan(library) is not None:
                state.scanned = now
                scanned.append(library.id)
        return scanned

    def _scan(self, library: Library) -> ScanResult | None:
        try:
            result = self.scan(library)
        except Exception as e:  # noqa: BLE001 - one library failing must not end the watching
            log.warning(f"Scan of library {library.name!r} after a change on disk failed: {e}")
            return None
        log.info(
            f"Library {library.name!r} changed on disk and was scanned "
            f"({result.added} added, {result.missing} missing, {result.total} on disk)"
        )
        try:
            self.after_scan(library)
        except Exception as e:  # noqa: BLE001
            log.warning(f"Matching metadata for library {library.name!r} failed: {e}")
        return result


def _scrape_what_is_missing(library: Library) -> None:
    if any(needs_scrape(g) for g in db_game_handler.get_games_for_library(library.id)):
        scrape_library_in_background(library.id)


def run(
    stop: threading.Event, interval: float = LIBRARY_WATCH_INTERVAL, watcher: LibraryWatcher | None = None
) -> None:
    """The watching loop, until `stop` is set."""
    watcher = watcher or LibraryWatcher()
    while not stop.wait(interval):
        try:
            watcher.poll()
        except Exception as e:  # noqa: BLE001 - the loop outlives any one bad poll
            log.warning(f"Watching the libraries failed: {e}")


def start() -> threading.Event | None:
    """Start watching in the background; the event stops it. None when watching is turned off."""
    if LIBRARY_WATCH_INTERVAL <= 0:
        return None
    stop = threading.Event()
    threading.Thread(target=run, args=(stop,), daemon=True, name="library-watcher").start()
    log.info(f"Watching the library folders for changes every {LIBRARY_WATCH_INTERVAL}s")
    return stop
