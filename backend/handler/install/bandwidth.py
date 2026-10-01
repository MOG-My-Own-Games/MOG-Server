"""Global bandwidth cap for in-progress stream-install downloads.

One shared limit for the whole server: every concurrent "still installing"
file transfer draws from the same budget, admin-configurable at runtime.
RomM backs this with Redis because its API can run as several gunicorn
worker processes that don't share memory; MOG runs as one process, so a
plain in-process, asyncio-lock-guarded counter is enough (see
handler.install.streaming_mode for the same simplification on its flag).

Fixed-window admission control, not a literal token bucket: each wall-clock
second is its own budget; a caller that would push the window over budget
backs its reservation out and waits for the next window.
"""

from __future__ import annotations

import asyncio
import time

_limit_bytes_per_sec: int | None = None
_window_second: int | None = None
_window_used = 0
_lock = asyncio.Lock()


def set_bytes_per_second(value: int | None) -> None:
    """Update the configured global cap."""
    global _limit_bytes_per_sec
    _limit_bytes_per_sec = value if value and value > 0 else None


def get_bytes_per_second() -> int | None:
    """The currently configured cap, or None when unlimited."""
    return _limit_bytes_per_sec


async def acquire(n_bytes: int) -> None:
    """Block until `n_bytes` fit inside the current global budget.

    No-op when no limit is configured. Never partially grants: a caller
    either gets its whole reservation for the current second or waits for
    the next one, so a chunk's bytes are never split across two windows.
    """
    global _window_second, _window_used
    if n_bytes <= 0:
        return
    while True:
        limit = _limit_bytes_per_sec
        if limit is None:
            return

        window = int(time.time())
        async with _lock:
            if _window_second != window:
                _window_second = window
                _window_used = 0
            _window_used += n_bytes
            used = _window_used
            if used > limit:
                _window_used -= n_bytes
        if used <= limit:
            return
        await asyncio.sleep(max(window + 1 - time.time(), 0.01))
