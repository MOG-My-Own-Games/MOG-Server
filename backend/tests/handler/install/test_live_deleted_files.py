"""A file the installer makes and deletes during the install (a decompressor's scratch space, gigabytes of it) must not
stay in the install cache, in its manifest and in every client's download.

Caught live: `srep-virtual-memory.tmp` (13.9 GB) was hardlinked into the cache by the live manifest loop while the
installer wrote it; the installer then deleted its own name, the cache's stayed, and the final manifest listed it."""

import json
import os
import threading
import time
from pathlib import Path

from handler.install import runner
from handler.install.manifest import LiveManifestEntry, read_live_removed, write_live_manifest


def test_a_link_whose_original_the_installer_deleted_is_dropped(tmp_path):
    cache, prefix = tmp_path / "cache", tmp_path / "prefix"
    cache.mkdir()
    prefix.mkdir()
    (prefix / "scratch.tmp").write_bytes(b"x" * 100)
    (prefix / "game.bin").write_bytes(b"y" * 100)
    linked = {}
    for name in ("scratch.tmp", "game.bin"):
        os.link(prefix / name, cache / name)
        linked[cache / name] = prefix / name
    (cache / "written_straight_here.bin").write_bytes(b"z")  # not linked by the loop: not its to touch

    (prefix / "scratch.tmp").unlink()  # the installer cleans up after itself
    gone = runner._drop_deleted(linked)

    assert gone == [cache / "scratch.tmp"]
    assert not (cache / "scratch.tmp").exists()  # the cache no longer holds its bytes
    assert (cache / "game.bin").exists() and (cache / "written_straight_here.bin").exists()
    assert list(linked) == [cache / "game.bin"]  # forgotten, so it is not looked at again
    assert runner._drop_deleted(linked) == []


def test_originals_the_scan_just_listed_are_not_looked_at_again(tmp_path, monkeypatch):
    source, dest, lost = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    source.write_bytes(b"x")
    os.link(source, dest)
    linked = {dest: source}
    asked = []
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: asked.append(self) or real_exists(self))

    assert runner._drop_deleted(linked, {source}) == [] and asked == []  # listed: no stat of it
    assert runner._drop_deleted({dest: lost}, {source}) == [dest]  # not listed, and really gone
    assert asked == [lost]

    source.unlink()
    still_there = tmp_path / "d"
    still_there.write_bytes(b"x")
    assert runner._drop_deleted({tmp_path / "e": still_there}, set()) == []  # a walk that missed it is not believed


def test_a_link_already_removed_by_hand_is_forgotten_without_an_error(tmp_path):
    source = tmp_path / "a"
    dest = tmp_path / "b"
    linked = {dest: source}  # neither exists
    assert runner._drop_deleted(linked) == [dest] and linked == {}


def test_the_manifest_tells_what_was_removed(tmp_path):
    entry = LiveManifestEntry(path="g/a.bin", size_bytes=1, sealed_bytes=1, complete=False)
    write_live_manifest(tmp_path, {"g/a.bin": entry}, {"g/b.tmp", "g/a.tmp"})
    assert read_live_removed(tmp_path) == ["g/a.tmp", "g/b.tmp"]
    assert json.loads((tmp_path / runner.LIVE_MANIFEST_FILENAME).read_text())["files"][0]["path"] == "g/a.bin"

    write_live_manifest(tmp_path, {"g/a.bin": entry})  # nothing was removed
    assert read_live_removed(tmp_path) == []
    assert read_live_removed(tmp_path / "nowhere") == []


def test_the_live_loop_lists_a_scratch_file_then_drops_it_when_the_installer_deletes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LIVE_MANIFEST_INTERVAL", 0.02)
    prefix, cache = tmp_path / "prefix", tmp_path / "cache"
    game = prefix / "drive_c" / "Program Files" / "Some Game"
    game.mkdir(parents=True)
    cache.mkdir()
    (game / "Game.exe").write_bytes(b"MZ" + b"x" * 100)
    (game / "scratch.tmp").write_bytes(b"x" * 5000)

    stop = threading.Event()
    thread = threading.Thread(target=runner._live_manifest_loop, args=(cache, prefix, frozenset(), stop), daemon=True)
    thread.start()

    def listed() -> set[str]:
        try:
            return {f["path"] for f in json.loads((cache / runner.LIVE_MANIFEST_FILENAME).read_text())["files"]}
        except (OSError, ValueError):
            return set()

    def wait_for(condition) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.02)
        return False

    try:
        assert wait_for(lambda: any(p.endswith("scratch.tmp") for p in listed()))
        (game / "scratch.tmp").unlink()  # the installer is done with it
        assert wait_for(lambda: not any(p.endswith("scratch.tmp") for p in listed()))
        assert wait_for(lambda: any(p.endswith("scratch.tmp") for p in read_live_removed(cache)))
        assert any(p.endswith("Game.exe") for p in listed())  # the rest is untouched
        assert not list(cache.rglob("scratch.tmp"))  # and the bytes are not kept in the cache
    finally:
        stop.set()
        thread.join(5)
