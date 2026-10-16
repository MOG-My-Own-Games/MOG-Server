"""The notes of the running version: what the release build wrote next to the code (scripts/release_notes.py)."""

from __future__ import annotations

from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parents[1] / "changelog.md"


def read() -> str | None:
    """The notes as Markdown: "" when the version has nothing to list, None when this build carries no notes."""
    try:
        return CHANGELOG_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
