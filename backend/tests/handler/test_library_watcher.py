import os
import threading
from pathlib import Path
from types import SimpleNamespace

from handler import library_watcher as lw
from handler.scan_handler import ScanResult


def library(id=1, root="/library"):
    return SimpleNamespace(id=id, name=f"Lib {id}", root_path=root)


class World:
    """Library folders as the watcher sees them, and the scans it asks for."""

    def __init__(self, libs):
        self.libs = libs
        self.disk: dict[str, object] = {lib.root_path: ("v1",) for lib in libs}
        self.scans, self.after = [], []
        self.fail = False

    def watcher(self):
        def scan(lib):
            if self.fail:
                raise RuntimeError("database is locked")
            self.scans.append(lib.id)
            return ScanResult(added=1, missing=0, total=3)

        return lw.LibraryWatcher(
            libraries=lambda: self.libs,
            look=lambda root: self.disk.get(str(root)),
            scan=scan,
            after_scan=lambda lib: self.after.append(lib.id),
        )


def test_a_library_seen_for_the_first_time_is_scanned_once_it_has_settled():
    world = World([library()])
    watcher = world.watcher()
    assert watcher.poll() == [] and world.scans == []  # the first look only notes it
    assert watcher.poll() == [1] and world.scans == [1] and world.after == [1]
    assert watcher.poll() == [] and world.scans == [1]  # unchanged: nothing more


def test_a_change_waits_until_it_stops_changing():
    world = World([library()])
    watcher = world.watcher()
    watcher.poll()
    watcher.poll()
    world.disk["/library"] = ("v2",)  # a game is added
    assert watcher.poll() == []  # just changed: a copy may still be going on
    world.disk["/library"] = ("v3",)  # and it is still being written
    assert watcher.poll() == []
    assert watcher.poll() == [1]  # two polls alike: scan
    assert world.scans == [1, 1]


def test_a_change_that_is_undone_before_it_settles_needs_no_scan():
    world = World([library()])
    watcher = world.watcher()
    watcher.poll()
    watcher.poll()
    world.disk["/library"] = ("v2",)
    watcher.poll()
    world.disk["/library"] = ("v1",)  # back to what was scanned
    assert watcher.poll() == [] and watcher.poll() == []
    assert world.scans == [1]


def test_a_library_that_cannot_be_read_is_left_alone():
    world = World([library()])
    watcher = world.watcher()
    watcher.poll()
    watcher.poll()
    world.disk["/library"] = None  # the share is not mounted
    assert watcher.poll() == [] and watcher.poll() == []
    world.disk["/library"] = ("v1",)  # back, as it was: nothing to do
    assert watcher.poll() == [] and world.scans == [1]


def test_each_library_is_watched_on_its_own():
    world = World([library(1, "/a"), library(2, "/b")])
    watcher = world.watcher()
    watcher.poll()
    watcher.poll()
    world.disk["/b"] = ("v2",)
    watcher.poll()
    assert watcher.poll() == [2] and world.scans == [1, 2, 2]


def test_a_library_that_is_removed_is_forgotten_and_a_new_one_is_scanned():
    world = World([library(1, "/a")])
    watcher = world.watcher()
    watcher.poll()
    watcher.poll()
    world.libs = [library(2, "/b")]
    world.disk["/b"] = ("v1",)
    watcher.poll()
    assert watcher.poll() == [2] and 1 not in watcher.seen


def test_a_scan_that_fails_is_tried_again_at_the_next_poll():
    world = World([library()])
    watcher = world.watcher()
    world.fail = True
    watcher.poll()
    assert watcher.poll() == [] and world.after == []  # failed: not counted as scanned
    world.fail = False
    assert watcher.poll() == [1]


def test_a_failure_matching_metadata_does_not_undo_the_scan():
    world = World([library()])
    watcher = world.watcher()

    def boom(lib):
        raise RuntimeError("IGDB is down")

    watcher.after_scan = boom
    watcher.poll()
    assert watcher.poll() == [1]
    assert watcher.poll() == []  # scanned all the same


def test_the_signature_sees_games_added_removed_resized_and_touched(tmp_path):
    (tmp_path / "Game A").mkdir()
    (tmp_path / "Game A" / "setup.exe").write_bytes(b"x")
    (tmp_path / "loose.zip").write_bytes(b"abc")
    base = lw.signature(tmp_path)
    assert [entry[0] for entry in base] == ["Game A", "loose.zip"]
    assert lw.signature(tmp_path) == base  # looking changes nothing

    (tmp_path / "Game B").mkdir()
    added = lw.signature(tmp_path)
    assert added != base

    (tmp_path / "loose.zip").write_bytes(b"abcdef")
    assert lw.signature(tmp_path) != added  # resized

    before = lw.signature(tmp_path)
    (tmp_path / "Game A" / "patch.exe").write_bytes(b"y")
    os.utime(tmp_path / "Game A", ns=(10**18, 10**18))
    assert lw.signature(tmp_path) != before  # a file added inside a game folder

    (tmp_path / "Game B").rmdir()
    assert "Game B" not in [e[0] for e in lw.signature(tmp_path)]


def test_a_folder_that_does_not_exist_has_no_signature(tmp_path):
    assert lw.signature(tmp_path / "unmounted") is None
    assert lw.signature(Path("/proc/self/environ")) is None  # a file is not a folder


def test_the_loop_polls_until_stopped_and_survives_a_bad_poll():
    polls = []
    stop = threading.Event()

    class Watcher:
        def poll(self):
            polls.append(1)
            if len(polls) == 1:
                raise RuntimeError("boom")
            if len(polls) >= 3:
                stop.set()
            return []

    lw.run(stop, interval=0.01, watcher=Watcher())
    assert len(polls) >= 3


def test_watching_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(lw, "LIBRARY_WATCH_INTERVAL", 0)
    assert lw.start() is None
