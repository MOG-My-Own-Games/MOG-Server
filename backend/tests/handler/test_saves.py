import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from handler import saves
from handler.database import db_saves_handler
from utils.archive_safety import ArchiveError


@pytest.fixture(autouse=True)
def _saves_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "SAVES_BASE_PATH", str(tmp_path / "saves"))
    monkeypatch.setattr(saves, "SAVES_KEEP_VERSIONS", 3)


def _incoming(tmp_path: Path, files: dict[str, bytes], name: str = "up.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for member, data in files.items():
            z.writestr(member, data)
    return path


def _store(tmp_path, content: bytes, device_id=1, game_id=5, user_id=1, trigger="quit"):
    incoming = _incoming(
        tmp_path, {"users/USER/Saved Games/a.sav": content}, f"up-{content!r}-{device_id}.zip"
    )
    return saves.store_version(user_id, game_id, device_id, trigger, incoming)


def test_a_version_is_filed_under_user_game_and_device(db, tmp_path):
    version, created = _store(tmp_path, b"one")

    assert created is True
    assert version.file_path.startswith("1/5/1/") and version.file_path.endswith(".zip")
    assert saves.resolve_path(version.file_path).is_file()
    assert (version.trigger, version.file_count) == ("quit", 1)
    assert version.manifest == [{"path": "users/USER/Saved Games/a.sav", "size": 3}]
    assert list((tmp_path / "saves").rglob("*.zip")) == [saves.resolve_path(version.file_path)]


def test_the_same_content_again_is_not_stored(db, tmp_path):
    first, _ = _store(tmp_path, b"one")
    again, created = _store(tmp_path, b"one")

    assert created is False and again.id == first.id
    assert len(db_saves_handler.get_for_game(1, 5)) == 1
    assert len(list((tmp_path / "saves").rglob("*.zip"))) == 1  # the upload was discarded too


def test_only_the_newest_versions_of_a_device_are_kept(db, tmp_path):
    stored = [_store(tmp_path, str(n).encode())[0] for n in range(5)]
    other, _ = _store(tmp_path, b"elsewhere", device_id=2)

    rows = db_saves_handler.get_for_game(1, 5)
    kept_by_device = {d: sorted(v.id for v in rows if v.device_id == d) for d in (1, 2)}

    assert kept_by_device[1] == [s.id for s in stored[2:]]
    assert kept_by_device[2] == [other.id]
    assert [saves.resolve_path(s.file_path).exists() for s in stored] == [False, False, True, True, True]
    assert saves.resolve_path(other.file_path).is_file()


def test_versions_of_another_game_or_user_do_not_count_against_the_limit(db, tmp_path):
    for n in range(3):
        _store(tmp_path, str(n).encode())
    _store(tmp_path, b"x", game_id=6)
    _store(tmp_path, b"y", user_id=2)

    assert len(db_saves_handler.get_for_game(1, 5)) == 3
    assert len(db_saves_handler.get_for_game(1, 6)) == 1 and len(db_saves_handler.get_for_game(2, 5)) == 1


def test_an_unacceptable_archive_is_refused_and_leaves_nothing(db, tmp_path):
    incoming = _incoming(tmp_path, {"../evil.sav": b"x"})
    with pytest.raises(ArchiveError):
        saves.store_version(1, 5, 1, "manual", incoming)
    assert not incoming.exists()
    assert db_saves_handler.get_for_game(1, 5) == []
    assert not list((tmp_path / "saves").rglob("*.zip"))


def test_removing_a_version_removes_its_file(db, tmp_path):
    version, _ = _store(tmp_path, b"one")
    path = saves.resolve_path(version.file_path)
    saves.remove_version(version)
    assert not path.exists() and db_saves_handler.get_version(version.id) is None


def test_a_stored_path_cannot_leave_the_saves_directory(tmp_path):
    with pytest.raises(ValueError):
        saves.resolve_path("../outside.zip")
    with pytest.raises(ValueError):
        saves.resolve_path("/etc/passwd")


def test_an_upload_over_the_limit_is_stopped_and_cleaned_up(monkeypatch, tmp_path):
    monkeypatch.setattr(saves, "MAX_SAVE_UPLOAD_BYTES", 10)
    with pytest.raises(saves.UploadTooLarge):
        asyncio.run(saves.receive_upload(UploadFile(io.BytesIO(b"x" * 11))))
    assert not list((tmp_path / "saves" / ".incoming").iterdir())

    received = asyncio.run(saves.receive_upload(UploadFile(io.BytesIO(b"x" * 10))))
    assert received.read_bytes() == b"x" * 10
