# Adapted from RomM (https://github.com/rommapp/romm), AGPL-3.0-or-later.
"""Registry and manager of Proton/Wine builds for the install sandbox.

Each Proton build lives in its own subdirectory under PROTON_INSTALL_ROOT
(default /opt/proton). The manager discovers them at runtime by scanning for
an executable ``proton`` (or ``bin/wine``) binary in each subdirectory, so
build identity is always tied to what actually exists on disk.

Builds not yet downloaded are still listed (sourced from GitHub release APIs)
so a client can offer a "download" affordance. Downloaded builds are
extracted into the same PROTON_INSTALL_ROOT tree and picked up on the next scan.

Unlike RomM (API and install worker in separate containers, builds tracked in
a shared Redis set so one container can see what the other downloaded), MOG
is a single process/container: disk is the only source of truth, and a
download just runs in a background thread with its progress kept in an
in-process dict - no cross-process registry needed.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import INSTALL_DEFAULT_PROTON_BUILD, PROTON_INSTALL_ROOT
from logger.formatter import highlight as hl
from logger.logger import log


def default_build_id() -> str | None:
    """The configured default Proton/Wine build id - the DB setting
    (editable from Settings in the web UI) takes priority over the env var,
    which stays as the bootstrap/.env-only fallback (same pattern as
    igdb_handler._credentials and utils.install_cache.default_ttl_days)."""
    from handler.database import db_settings_handler

    configured = db_settings_handler.get_settings().install_default_proton_build
    return configured or INSTALL_DEFAULT_PROTON_BUILD


@dataclass(frozen=True, slots=True)
class ProtonBuild:
    """One Proton build the server knows about."""

    id: str
    label: str
    installed: bool
    # Human-readable version string from the upstream release (e.g. "10-34").
    version: str | None = None
    # Where this build lives on disk right now, if installed. None when
    # installed=False.
    path: str | None = None
    # "runtime" = discovered on disk, "upstream" = only downloadable.
    source: str = "runtime"
    # Download URL for the release tarball, only set for upstream builds.
    download_url: str | None = None
    # Approximate tarball size in bytes, only set for upstream builds.
    size_bytes: int | None = None


def _scan_proton_root(root: str) -> list[ProtonBuild]:
    """Discover installed builds by scanning ``root`` one level deep.

    Each subdirectory that contains an executable ``proton`` launcher (Proton)
    or ``bin/wine`` (plain Wine) is treated as one build. The directory name
    is the build id.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    builds: list[ProtonBuild] = []
    for entry in sorted(root_path.iterdir()):
        if not entry.is_dir():
            continue
        proton_bin = entry / "proton"
        wine_bin = entry / "bin" / "wine"
        if proton_bin.exists() and os.access(proton_bin, os.X_OK):
            # Only surface builds whose full file tree is present - a partial
            # extraction (e.g. from an interrupted download) passes the
            # proton-binary check but is unusable at runtime.
            if not _is_proton_build_complete(entry):
                log.debug(f"Skipping incomplete Proton build at {entry}")
                continue
            builds.append(
                ProtonBuild(id=entry.name, label=entry.name, installed=True, path=str(proton_bin))
            )
        elif wine_bin.exists() and os.access(wine_bin, os.X_OK):
            builds.append(
                ProtonBuild(id=entry.name, label=entry.name, installed=True, path=str(wine_bin))
            )
    return builds


def _discover_installed() -> list[ProtonBuild]:
    return _scan_proton_root(PROTON_INSTALL_ROOT)


# --- Remote source discovery (downloadable builds) ---

# Fixed-id upstream sources: (github_repo, asset_suffix, build_id, label).
# The id stays stable as new releases appear ("always fetch the latest"), so
# the extracted directory is reused until the user removes it.
_LATEST_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("CachyOS/proton-cachyos", "tar.xz", "cachyos-latest", "Proton-CachyOS Latest"),
)
_STATIC_LABELS = {build_id: label for _, _, build_id, label in _LATEST_SOURCES}

# GE-Proton: the newest release of each major version >= _GE_MIN_MAJOR. The
# id is the release tag (e.g. "GE-Proton11-7") so a pinned build stays valid.
_GE_REPO = "GloriousEggroll/proton-ge-custom"
_GE_TAG_RE = re.compile(r"^GE-Proton(\d+)-(\d+)$")
_GE_MIN_MAJOR = 8
# 100 releases per page; the oldest major we list sits a few pages back.
_GE_MAX_PAGES = 6


def _github_get(url: str, params: dict[str, object] | None = None):
    import httpx

    try:
        resp = httpx.get(url, params=params, timeout=30, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.debug(f"GitHub fetch failed for {url}: {e}")
        return None


def _fetch_latest_source(repo: str, suffix: str, build_id: str, label: str) -> list[ProtonBuild]:
    """The newest release of a repo whose builds share one fixed id."""
    data = _github_get(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = (data or {}).get("tag_name", "")
    if not tag:
        return []
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(suffix) and "x86_64" in name:
            return [
                ProtonBuild(
                    id=build_id,
                    label=label,
                    installed=False,
                    version=tag,
                    source="upstream",
                    download_url=asset.get("browser_download_url"),
                    size_bytes=asset.get("size"),
                )
            ]
    return []


def _fetch_ge_latest_per_major() -> list[ProtonBuild]:
    """Newest GE-Proton release of every major version >= _GE_MIN_MAJOR."""
    best: dict[int, tuple[int, dict]] = {}
    for page in range(1, _GE_MAX_PAGES + 1):
        releases = _github_get(
            f"https://api.github.com/repos/{_GE_REPO}/releases", {"per_page": 100, "page": page}
        )
        if not releases:
            break
        for release in releases:
            match = _GE_TAG_RE.match(release.get("tag_name", ""))
            if not match or release.get("prerelease") or release.get("draft"):
                continue
            major, minor = int(match.group(1)), int(match.group(2))
            if major >= _GE_MIN_MAJOR and (major not in best or minor > best[major][0]):
                best[major] = (minor, release)
        if best and min(best) <= _GE_MIN_MAJOR:
            break

    builds: list[ProtonBuild] = []
    for major in sorted(best, reverse=True):
        release = best[major][1]
        asset = next(
            (a for a in release.get("assets", []) if a.get("name", "").endswith(".tar.gz")), None
        )
        if asset is None:
            continue
        tag = release["tag_name"]
        builds.append(
            ProtonBuild(
                id=tag,
                label=f"GE-Proton{major} (latest)",
                installed=False,
                version=tag,
                source="upstream",
                download_url=asset.get("browser_download_url"),
                size_bytes=asset.get("size"),
            )
        )
    return builds


def _is_proton_build_complete(build_dir: Path) -> bool:
    """Whether a Proton build directory has been fully extracted."""
    return (
        (build_dir / "proton").exists()
        and (build_dir / "files" / "bin" / "wine").exists()
        and (build_dir / "files" / "share" / "default_pfx").exists()
    )


def _fetch_upstream() -> list[ProtonBuild]:
    builds: list[ProtonBuild] = []
    for repo, suffix, build_id, label in _LATEST_SOURCES:
        builds.extend(_fetch_latest_source(repo, suffix, build_id, label))
    builds.extend(_fetch_ge_latest_per_major())
    return builds


# --- Download (runs in a background thread) ---

# build_id -> (downloaded_bytes, total_bytes). Absent means "not downloading".
_DOWNLOAD_PROGRESS: dict[str, tuple[int, int]] = {}
# build_id -> True while the tarball is being unpacked (progress stays at the
# last downloaded fraction during this phase; a client can show "Extracting...").
_EXTRACTING: set[str] = set()
_DOWNLOAD_LOCK = threading.Lock()


def enqueue_download(build_id: str) -> str:
    """Start a Proton download in the background. Idempotent: returns
    immediately if this build is already downloading."""
    with _DOWNLOAD_LOCK:
        if build_id in _DOWNLOAD_PROGRESS:
            return build_id
        _DOWNLOAD_PROGRESS[build_id] = (0, 0)
    threading.Thread(target=_download_proton_build, args=(build_id,), daemon=True).start()
    return build_id


def get_download_progress(build_id: str) -> float | None:
    """Download progress (0.0-1.0) for a build, or None if not downloading."""
    progress = _DOWNLOAD_PROGRESS.get(build_id)
    if progress is None:
        return None
    downloaded, total = progress
    if total == 0:
        return 0.0
    return min(1.0, downloaded / total)


def is_extracting(build_id: str) -> bool:
    return build_id in _EXTRACTING


def _download_proton_build(build_id: str) -> None:
    """Download + extract one Proton build, then register it on disk."""
    import httpx

    match = next((b for b in _fetch_upstream() if b.id == build_id), None)
    if match is None or not match.download_url:
        log.error(f"Cannot download Proton build {build_id}: no matching release found")
        _DOWNLOAD_PROGRESS.pop(build_id, None)
        return

    dest = Path(PROTON_INSTALL_ROOT) / build_id
    if dest.exists():
        if _is_proton_build_complete(dest):
            log.info(f"Proton build {build_id} already present at {dest}, skipping download")
            _DOWNLOAD_PROGRESS.pop(build_id, None)
            return
        log.warning(f"Removing incomplete Proton build at {dest}")
        shutil.rmtree(dest, ignore_errors=True)

    # Extract into a temp dir first, then rename - this makes the download
    # atomic: if the thread is killed mid-extraction, the temp dir is left
    # for cleanup and the next run starts fresh instead of finding a
    # half-extracted build and skipping the extraction.
    tmp_extract_dir = dest.parent / f".extract-{build_id}-{os.getpid()}"
    tmp_extract_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = dest.parent / f".download-{build_id}.tmp"

    log.info(f"Downloading Proton {hl(build_id)} from {hl(match.download_url)}")
    total = match.size_bytes or 0
    downloaded = 0
    try:
        with httpx.Client(timeout=600, follow_redirects=True) as client:
            with client.stream("GET", str(match.download_url)) as resp:
                resp.raise_for_status()
                with tmp_file.open("wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        _DOWNLOAD_PROGRESS[build_id] = (downloaded, total)
    except Exception as e:  # noqa: BLE001 - surfaced via log, download thread has no caller to raise to
        log.error(f"Proton download {build_id} failed: {e}")
        tmp_file.unlink(missing_ok=True)
        tmp_extract_dir.rmdir()
        _DOWNLOAD_PROGRESS.pop(build_id, None)
        return

    # Proton tarballs expand to a top-level dir like "GE-Proton11-7-x86_64",
    # but the binary needs to land at <dest>/proton, so strip the first path
    # component.
    log.info(f"Extracting Proton {hl(build_id)} into {hl(str(tmp_extract_dir))}")
    _EXTRACTING.add(build_id)
    try:
        with tarfile.open(tmp_file) as tar:
            members = []
            for m in tar.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) > 1:
                    m.name = parts[1]
                    members.append(m)
            tar.extractall(tmp_extract_dir, members=members, filter="data")
    except Exception:
        shutil.rmtree(tmp_extract_dir, ignore_errors=True)
        _EXTRACTING.discard(build_id)
        _DOWNLOAD_PROGRESS.pop(build_id, None)
        return
    finally:
        tmp_file.unlink(missing_ok=True)

    tmp_extract_dir.rename(dest)
    _EXTRACTING.discard(build_id)
    _DOWNLOAD_PROGRESS.pop(build_id, None)
    log.info(f"Proton {hl(build_id)} installed successfully")


def remove_build(build_id: str) -> None:
    """Remove a runtime-downloaded Proton build from disk."""
    dest = Path(PROTON_INSTALL_ROOT) / build_id
    if not dest.exists():
        return
    shutil.rmtree(dest)
    log.info(f"Removed Proton build {hl(build_id)}")


# --- Public API ---


def list_proton_builds() -> tuple[ProtonBuild, ...]:
    """Every build the server knows about: installed (discovered on disk)
    plus downloadable (from upstream release APIs)."""
    installed = _discover_installed()
    installed_ids = {b.id for b in installed}
    downloadable = _cached_upstream()
    downloadable_by_id = {b.id: b for b in downloadable}
    merged: list[ProtonBuild] = []
    for build in installed:
        upstream = downloadable_by_id.get(build.id)
        if upstream:
            merged.append(
                ProtonBuild(
                    id=build.id,
                    label=upstream.label,
                    installed=True,
                    version=upstream.version,
                    path=build.path,
                    download_url=upstream.download_url,
                    size_bytes=upstream.size_bytes,
                )
            )
        else:
            merged.append(
                ProtonBuild(
                    id=build.id,
                    label=_STATIC_LABELS.get(build.id, build.label),
                    installed=True,
                    version=build.version,
                    path=build.path,
                    source=build.source,
                )
            )
    merged.extend(b for b in downloadable if b.id not in installed_ids)
    return tuple(merged)


def resolve_proton_path(build_id: str | None) -> str | None:
    """The binary to run for a chosen build id.

    Returns None when the id is unset, unknown, or not installed - callers
    fall back to the server default (Wine) rather than failing the install.
    """
    if build_id is None:
        return None
    for build in _discover_installed():
        if build.id == build_id and build.installed:
            return build.path
    return None


def resolve_effective_build(explicit_id: str | None) -> str | None:
    """The Proton build id a session will actually run under, decided as early
    as session creation rather than left implicit inside the runner.

    None only when nothing at all is known (no explicit choice, no default
    configured, nothing installed) - the runner then falls back to plain Wine.
    """
    if explicit_id:
        return explicit_id
    default_id = default_build_id()
    if default_id:
        return default_id
    for build in _discover_installed():
        if build.installed:
            return build.id
    return None


# Cache the upstream (downloadable) list for this long to avoid hammering the
# GitHub API on every /proton-builds request.
_DOWNLOADABLE_CACHE_TTL = 3600
_DOWNLOADABLE_CACHE: tuple[float, list[ProtonBuild]] | None = None


def _cached_upstream() -> list[ProtonBuild]:
    global _DOWNLOADABLE_CACHE
    now = time.monotonic()
    if _DOWNLOADABLE_CACHE is not None:
        ts, cached = _DOWNLOADABLE_CACHE
        if now - ts < _DOWNLOADABLE_CACHE_TTL:
            return cached
    cached = _fetch_upstream()
    # An empty result is usually a rate limit or outage; retry sooner.
    if cached:
        _DOWNLOADABLE_CACHE = (now, cached)
    return cached
