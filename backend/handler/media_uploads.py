"""The artwork a person uploads for a game (cover, banner, hero, logo, icon), kept on this server next to the providers' own.

An upload is checked (a real PNG, JPEG, WebP or GIF of a sane size), stored under the resources folder as a PNG or JPEG
and chosen for its kind like a provider's image: `game.media[kind]` is `{"url": "/resources/media/<game>/<file>", "source":
"upload"}`. The URL is relative to this server, as the avatars' are. Only the newest upload of a kind is kept."""

from __future__ import annotations

import hashlib
import io
import re
import shutil
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from config import RESOURCES_BASE_PATH
from handler.media import KINDS
from logger.logger import log
from utils.image_cache import _kind_of, _usable_everywhere

UPLOAD = "upload"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 50_000_000  # width x height: a bomb of a few kilobytes that unpacks to gigabytes is not an artwork
URL_PREFIX = "/resources/media/"
_ALLOWED = (".png", ".jpg", ".webp", ".gif")
_URL = re.compile(r"/resources/media/(?P<game>\d+)/(?P<name>(?:%s)-[0-9a-f]{12}\.(?:png|jpg))" % "|".join(KINDS))


class UploadRefused(ValueError):
    """The file is not an artwork this server will keep; the message says why."""


def media_dir() -> Path:
    return Path(RESOURCES_BASE_PATH) / "media"


def is_upload(url: str | None) -> bool:
    return bool(url) and _URL.fullmatch(url) is not None


def path_for(url: str | None) -> Path | None:
    """The file of an upload's URL, or None for any other URL (or one that would lead outside the folder)."""
    match = _URL.fullmatch(url or "")
    if match is None:
        return None
    path = media_dir() / match["game"] / match["name"]
    return path if path.resolve().is_relative_to(media_dir().resolve()) else None


def check(data: bytes) -> bytes:
    """`data` as the bytes to keep (WebP and GIF become PNG), or UploadRefused."""
    if not data:
        raise UploadRefused("The file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRefused(f"The image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    if _kind_of(data)[0] not in _ALLOWED:
        raise UploadRefused("Must be a PNG, JPEG, WebP or GIF image")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise UploadRefused("The image has too many pixels")
            image.load()  # a file that only starts like an image fails here
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as e:
        raise UploadRefused("The file is not a usable image") from e
    return _usable_everywhere(data)


def store(game_id: int, kind: str, data: bytes) -> str:
    """Keep the upload as the game's `kind` and return its URL. The earlier upload of that kind is removed."""
    if kind not in KINDS:
        raise UploadRefused(f"Unknown kind of artwork: {kind}")
    data = check(data)
    suffix = _kind_of(data)[0]
    folder = media_dir() / str(game_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{hashlib.sha1(data).hexdigest()[:12]}{suffix}"
    target = folder / name
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    for old in folder.glob(f"{kind}-*"):
        if old != target:
            old.unlink(missing_ok=True)
    return f"{URL_PREFIX}{game_id}/{name}"


def remove(url: str | None) -> None:
    """Delete the file of an upload's URL (any other URL is not ours and is left alone)."""
    path = path_for(url)
    if path is not None:
        path.unlink(missing_ok=True)


def purge(game_id: int) -> None:
    """Delete everything uploaded for a game (the game itself is going away)."""
    try:
        shutil.rmtree(media_dir() / str(game_id), ignore_errors=True)
    except OSError as e:
        log.warning(f"Could not remove the uploaded artwork of game {game_id}: {e}")


def uploaded_kinds(media: dict | None) -> set[str]:
    """The kinds of a game's `media` whose current choice is an upload."""
    return {kind for kind, chosen in (media or {}).items() if isinstance(chosen, dict) and chosen.get("source") == UPLOAD}
