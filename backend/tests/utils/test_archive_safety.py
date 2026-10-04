import stat
import zipfile
from pathlib import Path

import pytest

from utils import archive_safety
from utils.archive_safety import ArchiveError, inspect_archive


def _zip(path: Path, files: dict[str, bytes], compression=zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_reports_the_files_and_skips_directories(tmp_path):
    path = tmp_path / "s.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("users/USER/Saved Games/", b"")
        z.writestr("users/USER/Saved Games/slot1.sav", b"abc")
        z.writestr("users/USER/Documents/cfg.ini", b"xy")

    info = inspect_archive(path)

    assert info.file_count == 2 and info.uncompressed_bytes == 5
    assert dict(info.files) == {"users/USER/Saved Games/slot1.sav": 3, "users/USER/Documents/cfg.ini": 2}


def test_the_hash_follows_the_content_not_the_zip_bytes(tmp_path):
    a = _zip(tmp_path / "a.zip", {"x.sav": b"1", "y.sav": b"2"}, zipfile.ZIP_DEFLATED)
    b = _zip(tmp_path / "b.zip", {"y.sav": b"2", "x.sav": b"1"}, zipfile.ZIP_STORED)
    c = _zip(tmp_path / "c.zip", {"x.sav": b"1", "y.sav": b"3"})

    assert a.read_bytes() != b.read_bytes()
    assert inspect_archive(a).content_hash == inspect_archive(b).content_hash
    assert inspect_archive(a).content_hash != inspect_archive(c).content_hash


@pytest.mark.parametrize(
    "name", ["../evil.sav", "a/../../evil.sav", "/etc/passwd", "C:/Windows/x.dll", "a\\b.sav"]
)
def test_unsafe_paths_are_refused(tmp_path, name):
    with pytest.raises(ArchiveError, match="unsafe path"):
        inspect_archive(_zip(tmp_path / "s.zip", {name: b"x"}))


def test_a_symbolic_link_is_refused(tmp_path):
    path = tmp_path / "s.zip"
    with zipfile.ZipFile(path, "w") as z:
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "/etc/passwd")
    with pytest.raises(ArchiveError, match="symbolic link"):
        inspect_archive(path)


def test_something_that_is_not_a_zip_or_is_empty_is_refused(tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(ArchiveError, match="not a zip"):
        inspect_archive(junk)
    with pytest.raises(ArchiveError, match="no files"):
        inspect_archive(_zip(tmp_path / "empty.zip", {}))


def test_too_many_entries_or_too_much_data_are_refused(monkeypatch, tmp_path):
    path = _zip(tmp_path / "s.zip", {"a": b"x" * 20, "b": b"y"})
    monkeypatch.setattr(archive_safety, "MAX_MEMBERS", 1)
    with pytest.raises(ArchiveError, match="too many"):
        inspect_archive(path)
    monkeypatch.setattr(archive_safety, "MAX_MEMBERS", 10)
    monkeypatch.setattr(archive_safety, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(ArchiveError, match="expands"):
        inspect_archive(path)


def test_a_corrupt_member_is_refused(tmp_path):
    path = _zip(tmp_path / "s.zip", {"a.sav": b"hello world" * 50}, zipfile.ZIP_STORED)
    raw = bytearray(path.read_bytes())
    raw[raw.index(b"hello world") + 3] ^= 0xFF  # flips a payload byte, so the CRC no longer matches
    path.write_bytes(bytes(raw))
    with pytest.raises(ArchiveError, match="unreadable"):
        inspect_archive(path)
