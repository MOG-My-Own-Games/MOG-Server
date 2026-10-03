"""Server-side cache of the cover and screenshot images a game's metadata points to.

Clients far from the provider CDNs (or on a slow link) fetch them from this
server instead: the image is downloaded once, covers are shrunk to a sensible
size, and every later request is a small local file. Only URLs already stored on
a game are ever fetched, never one a request supplies.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image

from config import RESOURCES_BASE_PATH
from logger.logger import log

CACHE_DIR = Path(RESOURCES_BASE_PATH) / "imagecache"
COVER_MAX_HEIGHT = 600
FETCH_TIMEOUT = 20.0


def _shrink(data: bytes, max_height: int) -> bytes:
    """JPEG of at most `max_height` pixels tall; the original bytes if it will not decode."""
    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("RGB")
        if image.height > max_height:
            image = image.resize((round(image.width * max_height / image.height), max_height), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception as e:  # noqa: BLE001 - serve the original rather than fail
        log.warning(f"Could not resize a cached image: {e}")
        return data


def cached_image(url: str, max_height: int | None = None) -> Path | None:
    """Local file for `url`, downloading it on first use. None if it cannot be fetched."""
    if not url.startswith(("http://", "https://")):
        return None
    key = hashlib.sha1(f"{url}|{max_height}".encode()).hexdigest()
    path = CACHE_DIR / f"{key}.jpg"
    if path.is_file():
        return path
    try:
        response = httpx.get(url, timeout=FETCH_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as e:
        log.warning(f"Could not fetch image {url}: {e}")
        return None
    data = _shrink(response.content, max_height) if max_height else response.content
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path
