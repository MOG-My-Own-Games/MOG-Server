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
            after_scan=lambda lib, result: self.after.append(lib.id),
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

    def boom(lib, result):
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


# --- the switch in Settings > Libraries ------------------------------------------------------------


def _row(watch=None):
    return SimpleNamespace(watch_libraries=watch)


def test_watching_is_on_unless_switched_off_and_the_switch_wins_over_the_environment(monkeypatch):
    assert lw.enabled_in(_row()) is True and lw.enabled_in(_row(True)) is True and lw.enabled_in(_row(False)) is False
    monkeypatch.setattr(lw, "LIBRARY_WATCH_INTERVAL", 0)  # the environment says off, until the switch is touched
    assert lw.enabled_in(_row()) is False and lw.enabled_in(_row(True)) is True


def test_nothing_is_looked_at_while_the_switch_is_off_and_what_changed_meanwhile_is_found_when_it_is_on():
    world = World([library()])
    on = [True]
    watcher = world.watcher()
    watcher.enabled = lambda: on[0]
    watcher.poll()
    assert watcher.poll() == [1]  # scanned once

    on[0] = False
    world.disk["/library"] = ("v2",)  # a game is added while it is off
    assert watcher.poll() == [] and watcher.poll() == [] and world.scans == [1]

    on[0] = True
    assert watcher.poll() == []  # first look after: it has just changed
    assert watcher.poll() == [1] and world.scans == [1, 1]


def test_the_switch_is_read_at_every_poll_so_no_restart_is_needed(monkeypatch):
    from handler.database import db_settings_handler

    world = World([library()])
    rows = [_row(False)]
    monkeypatch.setattr(db_settings_handler, "get_settings", lambda: rows[0])
    watcher = lw.LibraryWatcher(libraries=lambda: world.libs, look=lambda r: world.disk.get(str(r)), scan=lambda lib: (world.scans.append(lib.id), ScanResult(1, 0, 3))[1], after_scan=lambda lib, res: None)
    assert watcher.poll() == [] and watcher.poll() == [] and world.scans == []
    rows[0] = _row(True)
    watcher.poll()
    assert watcher.poll() == [1]


def test_the_thread_runs_even_with_the_environment_off_so_the_switch_can_turn_it_on(monkeypatch):
    started = []
    monkeypatch.setattr(lw, "LIBRARY_WATCH_INTERVAL", 0)
    monkeypatch.setattr(lw.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: started.append(kw["args"][1])))
    stop = lw.start()
    assert isinstance(stop, threading.Event) and started == [lw.DEFAULT_INTERVAL]


def test_the_settings_endpoint_reports_and_saves_the_switch(db):
    import asyncio

    from endpoints import settings as endpoint
    from endpoints.responses.settings import SettingsUpdateForm

    admin = SimpleNamespace(id=1)
    assert asyncio.run(endpoint.get_settings(admin)).watch_libraries is True
    saved = asyncio.run(endpoint.update_settings(admin, SettingsUpdateForm(watch_libraries=False)))
    assert saved.watch_libraries is False and asyncio.run(endpoint.get_settings(admin)).watch_libraries is False
    assert asyncio.run(endpoint.update_settings(admin, SettingsUpdateForm(watch_libraries=True))).watch_libraries is True
