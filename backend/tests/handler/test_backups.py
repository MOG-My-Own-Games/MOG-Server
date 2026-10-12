import json
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from handler import backups


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A data volume (a database, avatars, uploaded artwork, saves, an image cache) and an empty backup volume."""
    data, volume = tmp_path / "mog", tmp_path / "backups"
    (data / "resources" / "avatars").mkdir(parents=True)
    (data / "resources" / "media" / "1").mkdir(parents=True)
    (data / "resources" / "imagecache").mkdir(parents=True)
    (data / "saves" / "1").mkdir(parents=True)
    (data / "resources" / "avatars" / "1.png").write_bytes(b"avatar")
    (data / "resources" / "media" / "1" / "cover-0123456789ab.png").write_bytes(b"cover")
    (data / "resources" / "imagecache" / "x.jpg").write_bytes(b"cache")
    (data / "saves" / "1" / "a.zip").write_bytes(b"save")
    db = data / "mog.db"
    with sqlite3.connect(db) as connection:
        connection.execute("create table alembic_version (version_num text)")
        connection.execute("insert into alembic_version values ('0001_old')")
        connection.execute("create table games (id integer, name text)")
        connection.execute("insert into games values (1, 'Some Game')")
    volume.mkdir()
    for name, value in (("SQLITE_PATH", str(db)), ("RESOURCES_BASE_PATH", str(data / "resources")), ("SAVES_BASE_PATH", str(data / "saves")), ("BACKUP_PATH", str(volume))):
        monkeypatch.setattr(backups, name, value)
    monkeypatch.setattr(backups, "last_error", None)
    return SimpleNamespace(data=data, volume=volume)


def _members(path: Path) -> list[str]:
    with tarfile.open(path) as tar:
        return sorted(m.name for m in tar.getmembers() if m.isfile())


NOON = datetime(2026, 10, 12, 12, 0, 0)


def test_a_manual_backup_holds_the_database_the_avatars_and_the_artwork_but_not_the_caches_or_saves(world):
    info = backups.create(backups.MANUAL, now=NOON)

    assert info["name"] == "mog-manual-20261012-120000.tar.gz" and info["kind"] == "manual" and not info["includes_saves"]
    members = _members(world.volume / info["name"])
    assert members == ["manifest.json", "mog.db", "resources/avatars/1.png", "resources/media/1/cover-0123456789ab.png"]
    assert [p.name for p in world.volume.iterdir()] == [info["name"]]  # no scratch left behind


def test_the_saves_come_along_only_when_asked_and_the_name_says_so(world):
    info = backups.create(backups.MANUAL, include_saves=True, now=NOON)

    assert info["name"].endswith("-saves.tar.gz") and info["includes_saves"]
    assert "saves/1/a.zip" in _members(world.volume / info["name"])


def test_the_database_in_a_backup_is_a_whole_working_copy_and_the_manifest_says_what_it_is(world, tmp_path):
    info = backups.create(backups.DAILY, note="a note", now=NOON)

    with tarfile.open(world.volume / info["name"]) as tar:
        tar.extractall(tmp_path / "out", filter="data")
    with sqlite3.connect(tmp_path / "out" / "mog.db") as copy:
        assert copy.execute("select name from games").fetchall() == [("Some Game",)]
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert (manifest["kind"], manifest["database_revision"], manifest["note"]) == ("daily", "0001_old", "a note")


def test_a_pre_update_backup_is_the_database_alone_so_it_is_quick(world):
    info = backups.create(backups.PRE_UPDATE, now=NOON)

    assert _members(world.volume / info["name"]) == ["manifest.json", "mog.db"]


def test_only_the_newest_of_each_kind_are_kept(world, monkeypatch):
    monkeypatch.setitem(backups.KEEP, backups.DAILY, 2)
    for day in (9, 10, 11, 12):
        backups.create(backups.DAILY, now=datetime(2026, 10, day, 3, 0, 0))
    backups.create(backups.MANUAL, now=datetime(2026, 10, 1, 3, 0, 0))

    names = [b["name"] for b in backups.list_backups()]

    assert names == ["mog-daily-20261012-030000.tar.gz", "mog-daily-20261011-030000.tar.gz", "mog-manual-20261001-030000.tar.gz"]


def test_without_a_backup_volume_nothing_is_made_and_the_reason_is_given(world, monkeypatch, tmp_path):
    monkeypatch.setattr(backups, "BACKUP_PATH", str(tmp_path / "missing"))

    assert "No backup volume" in backups.available()
    with pytest.raises(backups.BackupUnavailable):
        backups.create(backups.MANUAL)


def test_a_second_backup_while_one_runs_is_refused(world):
    assert backups._lock.acquire(blocking=False)
    try:
        with pytest.raises(backups.BackupBusy):
            backups.create(backups.MANUAL)
    finally:
        backups._lock.release()


def test_a_failed_backup_leaves_no_partial_file_and_is_remembered(world, monkeypatch):
    def boom(target):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(backups, "_copy_database", boom)

    with pytest.raises(sqlite3.OperationalError):
        backups.create(backups.MANUAL)

    assert list(world.volume.iterdir()) == [] and "failed" in backups.last_error


def test_a_backup_is_found_deleted_and_downloaded_only_by_its_exact_name(world):
    name = backups.create(backups.MANUAL, now=NOON)["name"]
    (world.volume / "notes.txt").write_text("not a backup")

    assert backups.path_of(name) == world.volume / name
    for bad in ("notes.txt", "../notes.txt", f"../{name}", "mog-manual-20261012-120000.tar.gz/../x", "mog-weekly-20261012-120000.tar.gz"):
        assert backups.path_of(bad) is None and backups.delete(bad) is False
    assert (world.volume / "notes.txt").exists()
    assert backups.delete(name) is True and backups.list_backups() == []


@pytest.mark.parametrize(
    ("now", "made", "due"),
    [
        (datetime(2026, 10, 12, 2, 59), [], False),  # before the hour
        (datetime(2026, 10, 12, 3, 0), [], True),
        (datetime(2026, 10, 12, 15, 0), [], True),  # the server was off at 03:00: made when it is up
        (datetime(2026, 10, 12, 15, 0), [datetime(2026, 10, 12, 3, 0)], False),  # already today's
        (datetime(2026, 10, 12, 15, 0), [datetime(2026, 10, 11, 3, 0)], True),
    ],
)
def test_the_daily_backup_is_due_from_the_hour_on_once_a_day(now, made, due):
    existing = [{"kind": "daily", "created_at": when} for when in made] + [{"kind": "manual", "created_at": now}]

    assert backups.daily_due(now, existing) is due


def test_the_switches_default_to_daily_on_and_saves_off():
    assert backups.daily_enabled(SimpleNamespace(backup_daily=None)) is True
    assert backups.daily_enabled(SimpleNamespace(backup_daily=False)) is False
    assert backups.saves_in_daily(SimpleNamespace(backup_saves=None)) is False
    assert backups.saves_in_daily(SimpleNamespace(backup_saves=True)) is True


def test_the_scheduler_makes_the_daily_backup_when_due_and_on_and_not_when_off(world, monkeypatch):
    made = []
    monkeypatch.setattr(backups, "create", lambda kind, include_saves=False, **k: made.append((kind, include_saves)))
    monkeypatch.setattr(backups, "daily_due", lambda *a, **k: True)
    monkeypatch.setattr(backups, "CHECK_EVERY", 0.0)

    class Once:
        """An event that lets the loop go round twice, then stops it."""

        def __init__(self):
            self.rounds = 0

        def wait(self, _seconds):
            self.rounds += 1
            return self.rounds > 2

    backups.run(Once(), lambda: SimpleNamespace(backup_daily=True, backup_saves=True))
    assert made == [("daily", True), ("daily", True)]

    made.clear()
    backups.run(Once(), lambda: SimpleNamespace(backup_daily=False, backup_saves=False))
    assert made == []


def test_a_start_that_migrates_backs_the_database_up_first_and_one_that_does_not_does_not(world, monkeypatch):
    monkeypatch.setattr(backups, "pending_migration", lambda: True)
    backups.before_update()
    assert [b["kind"] for b in backups.list_backups()] == ["pre-update"]

    monkeypatch.setattr(backups, "pending_migration", lambda: False)
    backups.before_update()
    assert len(backups.list_backups()) == 1


def test_the_database_is_at_an_old_revision_when_it_is_not_at_the_codes_head(world):
    assert backups.pending_migration() is True  # 0001_old is not the head of this code


def test_a_new_install_has_nothing_to_back_up_before_its_first_migration(world, tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "SQLITE_PATH", str(tmp_path / "none.db"))
    assert backups.pending_migration() is False
    (tmp_path / "empty.db").write_bytes(b"")
    monkeypatch.setattr(backups, "SQLITE_PATH", str(tmp_path / "empty.db"))
    assert backups.pending_migration() is False


def test_before_an_update_with_no_volume_or_a_failing_backup_the_start_goes_on(world, monkeypatch, tmp_path):
    monkeypatch.setattr(backups, "pending_migration", lambda: True)
    monkeypatch.setattr(backups, "BACKUP_PATH", str(tmp_path / "missing"))
    backups.before_update()  # no volume: a warning, no exception

    monkeypatch.setattr(backups, "BACKUP_PATH", str(world.volume))
    monkeypatch.setattr(backups, "_copy_database", lambda target: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    backups.before_update()  # a failure: logged, no exception
    assert backups.list_backups() == []


def test_the_archive_name_is_what_listing_reads_back(world):
    backups.create(backups.PRE_UPDATE, now=datetime(2026, 1, 2, 3, 4, 5))

    only = backups.list_backups()[0]

    assert (only["kind"], only["includes_saves"], only["created_at"].year) == ("pre-update", False, 2026)
