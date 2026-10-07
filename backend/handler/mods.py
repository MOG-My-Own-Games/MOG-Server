"""A game's mods, to be downloaded (the server never installs them).

Every top-level entry of the game's mods folder is one mod: a folder or an archive (or any single file).
`mods/mod1/mod1file.zip` and `mods/mod2.zip` are two mods, mod1 and mod2.mod1 is a folder, so it is zipped on the
fly into a cache, in a background thread with its progress kept for the client to show; an archive or a file is
served as it is."""

from __future__ import annotations

import os
import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from config import MODS_CACHE_MAX_AGE_HOURS, MODS_CACHE_PATH
from handler.filesystem.installer_detection import ARCHIVE_EXTENSIONS, is_mods_folder
from logger.logger import log
from models.game import Game

CHUNK = 1024 * 1024
KIND_FOLDER, KIND_ARCHIVE, KIND_FILE = "folder", "archive", "file"
# Already compressed: stored in the zip as they are, deflating them again only costs time.
STORED_EXTENSIONS = ARCHIVE_EXTENSIONS | {".jpg", ".jpeg", ".png", ".mp3", ".ogg", ".mp4", ".webm", ".pak", ".dll"}


@dataclass(frozen=True)
class Mod:
    name: str
    kind: str
    path: Path
    size_bytes: int
    file_count: int


def _mods_folders(game_root: Path) -> list[Path]:
    try:
        return sorted(p for p in game_root.iterdir() if p.is_dir() and is_mods_folder(p.name))
    except OSError:
        return []


def _measure(path: Path) -> tuple[int, int]:
    """(bytes, files) under a folder."""
    size = count = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                size += (Path(dirpath) / name).stat().st_size
                count += 1
            except OSError:
                continue
    return size, count


def list_mods(game_root: Path) -> list[Mod]:
    found: dict[str, Mod] = {}
    for folder in _mods_folders(game_root):
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in found:
                continue
            if entry.is_dir():
                size, count = _measure(entry)
                if count:  # an empty folder is not a mod
                    found[entry.name] = Mod(entry.name, KIND_FOLDER, entry, size, count)
            elif entry.is_file():
                kind = KIND_ARCHIVE if entry.suffix.lower() in ARCHIVE_EXTENSIONS else KIND_FILE
                found[entry.name] = Mod(entry.name, kind, entry, entry.stat().st_size, 1)
    return list(found.values())


def find_mod(game_root: Path, name: str) -> Mod | None:
    """The mod called `name`, looked up among the real entries, so nothing the client sends is joined into a path."""
    return next((m for m in list_mods(game_root) if m.name == name), None)


@dataclass
class ZipJob:
    state: str = "zipping"  # "zipping" | "ready" | "failed"
    bytes_done: int = 0
    bytes_total: int = 0
    path: Path | None = None
    error: str | None = None
    fingerprint: tuple = ()
    user_id: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_jobs: dict[tuple[int, str], ZipJob] = {}
_jobs_lock = threading.Lock()


def _fingerprint(folder: Path) -> tuple:
    """What a folder holds, to tell a zip made earlier is still good."""
    found = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in sorted(files):
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            found.append((str(full.relative_to(folder)), st.st_size, st.st_mtime_ns))
    return tuple(sorted(found))


def _zip_path(game_id: int, name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "mod"
    return Path(MODS_CACHE_PATH) / str(game_id) / f"{safe}.zip"


def _forget_old_zips() -> None:
    cutoff = time.time() - MODS_CACHE_MAX_AGE_HOURS * 3600
    root = Path(MODS_CACHE_PATH)
    if not root.is_dir():
        return
    for zip_file in root.rglob("*.zip*"):
        try:
            if zip_file.stat().st_mtime < cutoff:
                zip_file.unlink()
        except OSError:
            continue


def job_for(game_id: int, name: str) -> ZipJob | None:
    with _jobs_lock:
        return _jobs.get((game_id, name))


def start_zip(game_id: int, mod: Mod, user_id: int, on_done=None) -> ZipJob:
    """Zip the mod's folder in the background, unless an earlier zip of the same content is ready or one is running.
    `on_done(error_or_None)` is called from the thread when the work ends."""
    key = (game_id, mod.name)
    fingerprint = _fingerprint(mod.path)
    with _jobs_lock:
        job = _jobs.get(key)
        if job is not None and job.state == "zipping":
            return job
        if job is not None and job.state == "ready" and job.fingerprint == fingerprint and job.path and job.path.is_file():
            return job
        job = ZipJob(bytes_total=mod.size_bytes, fingerprint=fingerprint, user_id=user_id)
        _jobs[key] = job
    threading.Thread(target=_run_zip, args=(game_id, mod, job, on_done), daemon=True, name=f"zip-mod-{game_id}").start()
    return job


def _run_zip(game_id: int, mod: Mod, job: ZipJob, on_done) -> None:
    target = _zip_path(game_id, mod.name)
    part = target.with_name(target.name + ".part")
    error = None
    try:
        _forget_old_zips()
        part.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(part, "w", allowZip64=True) as archive:
            for dirpath, _dirs, files in os.walk(mod.path):
                for name in sorted(files):
                    full = Path(dirpath) / name
                    rel = f"{mod.name}/{full.relative_to(mod.path).as_posix()}"
                    method = zipfile.ZIP_STORED if full.suffix.lower() in STORED_EXTENSIONS else zipfile.ZIP_DEFLATED
                    try:
                        info = zipfile.ZipInfo.from_file(full, rel)
                        info.compress_type = method
                        with open(full, "rb") as source, archive.open(info, "w", force_zip64=True) as out:
                            while chunk := source.read(CHUNK):
                                out.write(chunk)
                                job.bytes_done += len(chunk)
                    except OSError as e:
                        log.warning(f"Skipped {full} while zipping the mod {mod.name!r}: {e}")
        part.replace(target)
        job.path, job.state = target, "ready"
    except Exception as e:  # noqa: BLE001 - reported to the client as the job's error
        error = str(e) or e.__class__.__name__
        part.unlink(missing_ok=True)
        job.state, job.error = "failed", error
        log.warning(f"Zipping the mod {mod.name!r} of game {game_id} failed: {e}")
    if on_done is not None:
        on_done(error)
