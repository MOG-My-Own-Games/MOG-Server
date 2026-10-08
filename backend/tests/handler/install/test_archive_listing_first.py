"""An archive is listed before it is unpacked, and an archive inside it is looked into by taking out just that file."""

import io
import shutil
import zipfile
from pathlib import Path

import pytest
from handler.install import archive_prescan as ap
from utils import archives


class Fake:
    """Stands in for 7z: `members` maps an archive's name to what a listing of it says (None: unreadable)."""

    def __init__(self, monkeypatch, members, inner_members=None):
        self.members, self.calls = members, []
        monkeypatch.setattr(ap, "try_list_archive_members", self.list)
        monkeypatch.setattr(ap, "extract_archive_member", self.member)
        monkeypatch.setattr(ap, "extract_archive_tree", self.tree)

    def list(self, path):
        self.calls.append(("list", Path(path).name))
        return self.members.get(Path(path).name)

    def member(self, archive, member, dest):
        self.calls.append(("member", member))
        target = Path(dest) / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"inner")
        return True

    def tree(self, archive, dest, exclude=()):
        self.calls.append(("tree", Path(archive).name, list(exclude)))
        for name, _size in self.members.get(Path(archive).name) or [("setup.exe", 1)]:
            if name not in exclude:
                (Path(dest) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(dest) / name).write_bytes(b"x")
        return True

    def kinds(self):
        return [c[0] for c in self.calls]


def test_an_archive_with_nothing_runnable_in_it_is_turned_down_without_unpacking(monkeypatch):
    fake = Fake(monkeypatch, {"a.zip": [("readme.txt", 5), ("data/level.pak", 900)]})
    assert ap.extract_and_rescan(Path("/x/a.zip")) is None
    assert fake.kinds() == ["list"]  # nothing was extracted


def test_an_installer_that_is_not_in_the_listing_is_turned_down_without_unpacking(monkeypatch):
    fake = Fake(monkeypatch, {"a.zip": [("setup.exe", 5)]})
    assert ap.extract_and_rescan(Path("/x/a.zip"), "other.exe") is None
    assert fake.kinds() == ["list"]


def test_an_archive_with_an_installer_is_unpacked_as_before(monkeypatch):
    fake = Fake(monkeypatch, {"a.zip": [("setup.exe", 5), ("data.bin", 9)]})
    temp, root, chosen = ap.extract_and_rescan(Path("/x/a.zip"))
    try:
        assert chosen.path == "setup.exe" and (root / "setup.exe").exists()
        assert fake.calls == [("list", "a.zip"), ("tree", "a.zip", [])]
    finally:
        temp.cleanup()


def test_an_archive_that_cannot_be_listed_is_unpacked_and_searched_as_before(monkeypatch):
    fake = Fake(monkeypatch, {"a.zip": None})
    temp, _root, chosen = ap.extract_and_rescan(Path("/x/a.zip"))
    try:
        assert chosen.path == "setup.exe" and ("tree", "a.zip", []) in fake.calls
    finally:
        temp.cleanup()


def test_an_archive_inside_one_with_no_installer_is_turned_down_before_the_outer_one_is_unpacked(monkeypatch):
    fake = Fake(monkeypatch, {"outer.zip": [("game.zip", 100), ("extra.txt", 1)], "game.zip": [("readme.txt", 1)]})
    assert ap.extract_and_rescan(Path("/x/outer.zip")) is None
    assert fake.kinds() == ["list", "member", "list"]  # just that file taken out and listed; no tree


def test_an_archive_inside_one_with_an_installer_is_taken_out_once_and_the_rest_unpacked_around_it(monkeypatch):
    fake = Fake(
        monkeypatch,
        {"outer.zip": [("game.zip", 100), ("extra.txt", 1)], "game.zip": [("setup.exe", 1)]},
    )
    monkeypatch.setattr(ap, "_unpack_nested", lambda root, archive: ap.InstallerCandidate(
        path=f"{archive.path}.extracted/setup.exe", file_name="setup.exe", file_size_bytes=1, rank=0, kind="known installer"))
    temp, root, chosen = ap.extract_and_rescan(Path("/x/outer.zip"))
    try:
        assert chosen.path == "game.zip.extracted/setup.exe"
        assert ("member", "game.zip") in fake.calls and ("tree", "outer.zip", ["game.zip"]) in fake.calls
        assert (root / "extra.txt").exists() and (root / "game.zip").exists()
    finally:
        temp.cleanup()


def test_when_the_nested_archive_was_all_there_was_nothing_else_is_unpacked(monkeypatch):
    fake = Fake(monkeypatch, {"outer.zip": [("game.zip", 100)], "game.zip": [("setup.exe", 1)]})
    monkeypatch.setattr(ap, "_unpack_nested", lambda root, archive: ap.InstallerCandidate(
        path="game.zip.extracted/setup.exe", file_name="setup.exe", file_size_bytes=1, rank=0, kind="known installer"))
    temp, _root, _chosen = ap.extract_and_rescan(Path("/x/outer.zip"))
    try:
        assert not any(c[0] == "tree" for c in fake.calls)
    finally:
        temp.cleanup()


def test_a_nested_archive_too_big_to_look_into_is_not_taken_out_and_is_found_out_by_unpacking(monkeypatch):
    fake = Fake(monkeypatch, {"outer.zip": [("game.iso", ap.PEEK_MAX_BYTES + 1)]})
    monkeypatch.setattr(ap, "_unpack_nested", lambda root, archive: ap.InstallerCandidate(
        path="game.iso.extracted/setup.exe", file_name="setup.exe", file_size_bytes=1, rank=0, kind="known installer"))
    temp, _root, _chosen = ap.extract_and_rescan(Path("/x/outer.zip"))
    try:
        assert "member" not in fake.kinds() and ("tree", "outer.zip", []) in fake.calls
    finally:
        temp.cleanup()


def test_a_nested_archive_that_cannot_be_taken_out_is_found_out_by_unpacking(monkeypatch):
    fake = Fake(monkeypatch, {"outer.zip": [("game.zip", 100)]})
    monkeypatch.setattr(ap, "extract_archive_member", lambda *a: False)
    monkeypatch.setattr(ap, "_unpack_nested", lambda root, archive: ap.InstallerCandidate(
        path="game.zip.extracted/setup.exe", file_name="setup.exe", file_size_bytes=1, rank=0, kind="known installer"))
    temp, _root, _chosen = ap.extract_and_rescan(Path("/x/outer.zip"))
    try:
        assert ("tree", "outer.zip", []) in fake.calls
    finally:
        temp.cleanup()


# --- listing that can tell "unreadable" from "empty" -------------------------------------------------


def test_an_unreadable_archive_is_not_the_same_as_an_empty_one(monkeypatch):
    import subprocess

    def broken(*a, **k):
        raise subprocess.CalledProcessError(2, "7z")

    monkeypatch.setattr(archives.subprocess, "run", broken)
    assert archives.try_list_archive_members(Path("x.zip")) is None
    assert archives.list_archive_members(Path("x.zip")) == []


# --- with the real 7z, where it is installed --------------------------------------------------------


def _zip(path: Path, files: dict) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return path


def _zipped(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.skipif(shutil.which("7z") is None, reason="7z is not installed here")
class TestWithTheRealSevenZip:
    def test_nothing_is_unpacked_for_an_archive_with_no_installer(self, tmp_path):
        assert ap.extract_and_rescan(_zip(tmp_path / "a.zip", {"readme.txt": "x"})) is None

    def test_a_nested_archive_with_an_installer_is_found_with_its_neighbours_unpacked(self, tmp_path):
        outer = _zip(tmp_path / "e.zip", {"Game [GOG] (1).zip": _zipped({"setup.exe": "MZ"}), "extra.txt": "y"})
        temp, root, chosen = ap.extract_and_rescan(outer)
        try:
            assert chosen.path == "Game [GOG] (1).zip.extracted/setup.exe"
            assert (root / "extra.txt").read_text() == "y" and (root / chosen.path).is_file()
        finally:
            temp.cleanup()

    def test_a_nested_archive_with_nothing_runnable_is_turned_down(self, tmp_path):
        assert ap.extract_and_rescan(_zip(tmp_path / "d.zip", {"inner.zip": _zipped({"readme.txt": "x"}), "extra.txt": "y"})) is None

    def test_a_nested_archive_alone_in_its_outer_one(self, tmp_path):
        temp, _root, chosen = ap.extract_and_rescan(_zip(tmp_path / "f.zip", {"inner.zip": _zipped({"setup.exe": "MZ"})}))
        try:
            assert chosen.path == "inner.zip.extracted/setup.exe"
        finally:
            temp.cleanup()
