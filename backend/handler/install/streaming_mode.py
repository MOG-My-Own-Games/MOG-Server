"""Whether the install runner may stream a large file's bytes as they're
written, instead of waiting for scan_live_manifest's normal "observed
stable" confirmation before exposing them.

Experimental and disabled by default. A plain in-process flag: RomM backs
this with Redis because its install worker and web API are separate
containers; MOG runs both in the same process, so there's nothing to
synchronize across (see handler.install.bandwidth for the same reasoning).
"""

from __future__ import annotations

_stream_uncompleted_files = False


def set_stream_uncompleted_files(enabled: bool) -> None:
    global _stream_uncompleted_files
    _stream_uncompleted_files = enabled


def stream_uncompleted_files_enabled() -> bool:
    return _stream_uncompleted_files
