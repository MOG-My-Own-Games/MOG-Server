import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from endpoints import install
from fastapi import HTTPException
from handler.install.manifest import LiveManifestEntry


def _call(monkeypatch, tmp_path: Path, entries: dict, range_header: str | None):
    monkeypatch.setattr(install, "_resolve_session", lambda *a: SimpleNamespace(id=1))
    monkeypatch.setattr(install, "session_cache_dir", lambda _id: tmp_path)
    monkeypatch.setattr(install, "read_live_manifest", lambda _dir: entries)
    request = SimpleNamespace(headers={"range": range_header} if range_header else {})
    user = SimpleNamespace(id=1)
    return asyncio.run(install.download_install_stream_file(user, 1, "game/a.bin", request))


def test_a_file_the_live_snapshot_lists_but_that_is_gone_is_a_404_not_a_cut_body(monkeypatch, tmp_path):
    entry = LiveManifestEntry(path="game/a.bin", size_bytes=100, sealed_bytes=100, complete=False)
    with pytest.raises(HTTPException) as err:
        _call(monkeypatch, tmp_path, {"game/a.bin": entry}, None)
    assert err.value.status_code == 404


def test_a_file_shorter_than_the_snapshot_says_is_served_only_as_far_as_it_goes(monkeypatch, tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "a.bin").write_bytes(b"x" * 40)
    entry = LiveManifestEntry(path="game/a.bin", size_bytes=100, sealed_bytes=100, complete=False)

    response = _call(monkeypatch, tmp_path, {"game/a.bin": entry}, None)

    assert response.status_code == 206 and response.headers["content-length"] == "40"


def test_a_range_past_what_is_really_there_is_a_416(monkeypatch, tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "a.bin").write_bytes(b"x" * 40)
    entry = LiveManifestEntry(path="game/a.bin", size_bytes=100, sealed_bytes=100, complete=False)

    response = _call(monkeypatch, tmp_path, {"game/a.bin": entry}, "bytes=60-")

    assert response.status_code == 416
