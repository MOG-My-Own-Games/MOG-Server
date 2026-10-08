"""Where games keep their save files, from the community Ludusavi manifest
(https://github.com/mtkennerly/ludusavi-manifest, MIT).

The manifest (17 MB of YAML) is downloaded in the background at startup and again every
LUDUSAVI_REFRESH_HOURS, never baked into the image. Until a download ends, the copy kept in the data
folder from the last run answers, and with none there is simply no answer. The file is read one game at a
time and reduced to the Linux save paths worth keeping, so a lookup is a dict read and loading it does not
need the whole file in memory.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import yaml

import config
from logger.logger import log

# The only placeholders a client on Linux can resolve: its home, the XDG folders and the game's own folder.
PLACEHOLDERS = frozenset({"<home>", "<xdgConfig>", "<xdgData>", "<base>"})
_PLACEHOLDER = re.compile(r"<[A-Za-z]+>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_TIMEOUT = httpx.Timeout(30.0, read=120.0)

_lock = threading.Lock()
_loaded: tuple[float, dict[str, list[str]]] | None = None  # (index file mtime, squashed name -> paths)


def _manifest_file() -> Path:
    return Path(config.LUDUSAVI_CACHE_PATH) / "manifest.yaml"


def _index_file() -> Path:
    return Path(config.LUDUSAVI_CACHE_PATH) / "index.json"


def _etag_file() -> Path:
    return Path(config.LUDUSAVI_CACHE_PATH) / "etag"


def squash(name: str) -> str:
    return _NON_ALNUM.sub("", name.lower())


def _applies_on_linux(entry: dict) -> bool:
    """A path with no condition, or with one that names Linux or no OS, is for Linux."""
    conditions = entry.get("when") or []
    return not conditions or any(c.get("os") in (None, "linux") for c in conditions if isinstance(c, dict))


def linux_save_paths(game: dict) -> list[str]:
    """The paths of one manifest entry that hold saves on Linux and that a client can resolve."""
    paths = []
    for path, entry in (game.get("files") or {}).items():
        if not isinstance(entry, dict) or "save" not in (entry.get("tags") or []) or not _applies_on_linux(entry):
            continue
        if set(_PLACEHOLDER.findall(path)) <= PLACEHOLDERS:
            paths.append(path)
    return sorted(set(paths))


def _games(manifest: Path) -> Iterator[tuple[str, dict]]:
    """(name, entry) for each game, parsing one block at a time: a block starts at a line with no indentation."""
    block: list[str] = []

    def parse() -> Iterator[tuple[str, dict]]:
        # Only entries with file paths can hold a Linux save, which skips most of the file unparsed.
        if block and any(line.startswith("  files:") for line in block):
            try:
                loaded = yaml.load("".join(block), Loader=_LOADER)
            except yaml.YAMLError:
                return
            if isinstance(loaded, dict):
                for name, game in loaded.items():
                    if isinstance(game, dict):
                        yield str(name), game

    with manifest.open(encoding="utf-8") as f:
        for line in f:
            if line[:1] not in (" ", "\n", "\r", "#") and not line.startswith("---"):
                yield from parse()
                block = []
            block.append(line)
        yield from parse()


def build_index(manifest: Path) -> dict[str, list[str]]:
    """Name -> Linux save paths, for every game of the manifest that has any."""
    index: dict[str, list[str]] = {}
    for name, game in _games(manifest):
        if paths := linux_save_paths(game):
            index.setdefault(squash(name), paths)
    return index


def _write_index(index: dict[str, list[str]]) -> None:
    target = _index_file()
    part = target.with_name(target.name + ".part")
    part.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    part.replace(target)


def refresh() -> bool:
    """Download the manifest if it changed since the copy kept and rebuild the index. Returns whether it did.
    A failed download changes nothing: what was kept keeps answering."""
    folder = Path(config.LUDUSAVI_CACHE_PATH)
    folder.mkdir(parents=True, exist_ok=True)
    headers = {}
    if _etag_file().is_file() and _manifest_file().is_file():
        headers["If-None-Match"] = _etag_file().read_text().strip()
    part = _manifest_file().with_name("manifest.yaml.part")
    try:
        with httpx.stream("GET", config.LUDUSAVI_MANIFEST_URL, headers=headers, timeout=_TIMEOUT, follow_redirects=True) as resp:
            if resp.status_code == 304:
                return False
            resp.raise_for_status()
            with part.open("wb") as out:
                for chunk in resp.iter_bytes(1024 * 1024):
                    out.write(chunk)
            etag = resp.headers.get("etag")
        index = build_index(part)
    except (httpx.HTTPError, OSError) as e:
        part.unlink(missing_ok=True)
        log.warning(f"Could not refresh the Ludusavi save manifest: {e}")
        return False
    part.replace(_manifest_file())
    _write_index(index)
    if etag:
        _etag_file().write_text(etag)
    log.info(f"Ludusavi save manifest refreshed: {len(index)} games with Linux save paths")
    return True


def _index() -> dict[str, list[str]]:
    """The index kept on disk, read again only when a refresh replaced it."""
    global _loaded
    try:
        mtime = _index_file().stat().st_mtime
    except OSError:
        return {}
    with _lock:
        if _loaded is None or _loaded[0] != mtime:
            try:
                _loaded = (mtime, json.loads(_index_file().read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                log.warning(f"Could not read the Ludusavi index: {e}")
                return {}
        return _loaded[1]


def paths_for(*names: str | None) -> list[str]:
    """The Linux save paths for a game known by any of these names (with placeholders), [] when the manifest has none."""
    index = _index()
    for name in names:
        if name and (paths := index.get(squash(name))):
            return paths
    return []


def start() -> threading.Event | None:
    """Keep the manifest fresh in the background; returns the event that stops it, or None when switched off."""
    if not config.LUDUSAVI_ENABLED:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                refresh()
            except Exception as e:  # noqa: BLE001 - a background refresh must never take the server down
                log.warning(f"Ludusavi refresh failed: {e}")
            stop.wait(config.LUDUSAVI_REFRESH_HOURS * 3600)

    threading.Thread(target=loop, daemon=True, name="ludusavi-refresh").start()
    return stop
