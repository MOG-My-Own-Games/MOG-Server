from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path


def iter_files(path: str, recursive: bool = False) -> Iterator[tuple[Path, str]]:
    """Yield (dir, file_name) for every file under `path`."""
    for root, _, files in os.walk(path, topdown=True):
        for file in files:
            yield Path(root), file
        if not recursive:
            break
