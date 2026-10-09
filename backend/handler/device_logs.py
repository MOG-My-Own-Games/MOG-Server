"""The log a client sends along with its saves, when its user allowed it: the newest one per device, as a plain file,
so a problem on a machine out of reach (a Steam Deck, say) can still be read."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from config import DEVICE_LOGS_PATH, MAX_DEVICE_LOG_BYTES


def log_path(user_id: int, device_id: int) -> Path:
    return Path(DEVICE_LOGS_PATH) / str(user_id) / f"{device_id}.log"


def store(user_id: int, device_id: int, data: bytes) -> int:
    """Keep `data` as the device's log (its newest MAX_DEVICE_LOG_BYTES, cut at a line). Returns the bytes kept."""
    if len(data) > MAX_DEVICE_LOG_BYTES:
        data = data[-MAX_DEVICE_LOG_BYTES:]
        data = data[data.find(b"\n") + 1 :]
    path = log_path(user_id, device_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return len(data)


def read(user_id: int, device_id: int) -> str | None:
    try:
        return log_path(user_id, device_id).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def uploaded_at(user_id: int, device_id: int) -> datetime | None:
    try:
        return datetime.fromtimestamp(log_path(user_id, device_id).stat().st_mtime, UTC)
    except OSError:
        return None
