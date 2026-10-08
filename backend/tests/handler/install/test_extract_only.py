import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from endpoints import install as endpoint
from endpoints.responses.install import InstallStartForm
from fastapi import HTTPException
from handler.filesystem.installer_detection import (
    DetectedFile,
    InstallerCandidate,
)
from handler.install import archive_prescan, runner
from handler.install.manifest import MANIFEST_FILENAME, read_manifest
from models.install_session import InstallSessionState


def cand(path, rank, kind="executable (nested)"):
    return InstallerCandidate(path=path, file_name=Path(path).name, file_size_bytes=1, rank=rank, kind=kind)


def listing(*paths):
    return archive_prescan.detect_installer_candidates([DetectedFile(p, 1000) for p in paths])


# --- when an archive looks like a game that needs no installer ---------------------------------------


def test_an_archive_with_the_game_buried_in_folders_suggests_extracting_it():
    found = listing(
        "Hearthlands.v1/Hearthlands.v1/Hearthlands.exe", "Hearthlands.v1/Hearthlands.v1/bin/java.exe"
    )
    assert archive_prescan.extract_suggested(found) is True


def test_an_archive_of_data_files_only_suggests_extracting_it():
    assert archive_prescan.extract_suggested([]) is True


@pytest.mark.parametrize(
    "paths",
    [
        ["setup.exe", "data.bin"],  # a known installer
        ["GOG-Game-Setup.exe"],
        ["Game/setup_game.exe"],  # a known name, nested
        ["Install Game.exe"],  # an executable at the top level might be the installer
    ],
)
def test_an_archive_that_has_an_installer_is_not_suggested(paths):
    found = [c for c in listing(*paths) if c.rank <= 2]
    assert archive_prescan.extract_suggested(found) is False


def test_an_archive_of_archives_is_unpacked_further_not_extracted_as_it_is():
    assert (
        archive_prescan.extract_suggested([cand("a.zip", 4, "archive"), cand("b.zip", 4, "archive")]) is False
    )
    assert archive_prescan.extract_suggested([cand("a.iso", 3, "disc image")]) is False


def test_the_bundled_prerequisites_do_not_count_as_an_installer():
    found = listing("Game/Game.exe", "Game/_CommonRedist/vcredist/setup.exe")
    assert archive_prescan.extract_suggested([c for c in found if c.rank <= 2]) is True


# --- the runner -------------------------------------------------------------------------------------


class FakeDb:
    def __init__(self, state=InstallSessionState.INSTALLING):
        self.session = SimpleNamespace(id=1, state=state)
        self.updates = []

    def get_session(self, _id):
        return self.session

    def update_session(self, _id, fields):
        self.updates.append(dict(fields))
        for key, value in fields.items():
            setattr(self.session, key, value)
        return self.session


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = FakeDb()
    work = tmp_path / "cache" / "1"
    work.mkdir(parents=True)
    source = tmp_path / "library" / "Hearthlands.rar"
    source.parent.mkdir()
    source.write_bytes(b"rar")
    monkeypatch.setattr(runner, "db_install_session_handler", db)
    monkeypatch.setattr(runner, "ensure_session_cache_dir", lambda _id: work)
    monkeypatch.setattr(runner.fs_game_handler, "resolve_installer_abs_path", lambda game, rel: str(source))
    monkeypatch.setattr(runner, "notify_auto_mode_failed", lambda *a: None)
    return SimpleNamespace(db=db, work=work, source=source)


def extractor(files, links=()):
    def extract(archive, dest):
        for rel, content in files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for rel, to in links:
            (dest / rel).symlink_to(to)
        return None if files else "no files"

    return extract


def test_the_archive_is_extracted_into_the_cache_and_becomes_the_install(env, monkeypatch):
    monkeypatch.setattr(
        runner,
        "extract_archive",
        extractor({"Hearthlands/Hearthlands.exe": b"x" * 10, "Hearthlands/data/a.bin": b"y" * 5}),
    )
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert env.db.session.state == InstallSessionState.DONE
    manifest = read_manifest(env.work)
    assert {e.path: e.size_bytes for e in manifest} == {
        "Hearthlands/Hearthlands.exe": 10,
        "Hearthlands/data/a.bin": 5,
    }
    assert env.db.session.bytes_total == 15 and env.db.session.cache_path == str(env.work)
    states = [u["state"] for u in env.db.updates if "state" in u]
    assert states == [InstallSessionState.INSTALLING, InstallSessionState.STREAMING, InstallSessionState.DONE]
    assert env.db.updates[0]["phase_detail"] == "Hearthlands.rar"
    assert not any(e.path == MANIFEST_FILENAME for e in manifest)


def test_a_link_inside_the_archive_is_removed_not_followed(env, monkeypatch, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve")
    monkeypatch.setattr(
        runner, "extract_archive", extractor({"game.exe": b"x"}, links=[("leak.txt", outside)])
    )
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert [e.path for e in read_manifest(env.work)] == ["game.exe"]
    assert not (env.work / "leak.txt").exists() and outside.exists()


def test_a_failed_extraction_fails_the_session_with_the_reason(env, monkeypatch):
    monkeypatch.setattr(runner, "extract_archive", lambda archive, dest: "7-Zip: ERROR: Unsupported Method")
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert (
        env.db.session.state == InstallSessionState.FAILED
        and "Could not extract Hearthlands.rar" in env.db.session.error
    )


def test_an_archive_with_no_files_fails(env, monkeypatch):
    def only_dirs(archive, dest):
        (dest / "empty").mkdir()
        return None

    monkeypatch.setattr(runner, "extract_archive", only_dirs)
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert env.db.session.state == InstallSessionState.FAILED and "no files" in env.db.session.error


def test_a_session_cancelled_while_unpacking_is_left_cancelled(env, monkeypatch):
    def cancelled_midway(archive, dest):
        (dest / "a.exe").write_bytes(b"x")
        env.db.session.state = InstallSessionState.FAILED  # what the cancel endpoint does
        env.db.session.error = "Cancelled"
        return None

    monkeypatch.setattr(runner, "extract_archive", cancelled_midway)
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert env.db.session.state == InstallSessionState.FAILED and env.db.session.error == "Cancelled"
    assert read_manifest(env.work) is None


def _game_folder(env, monkeypatch, tmp_path, files):
    root = tmp_path / "library" / "Metroid"
    for rel, content in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(content)
    monkeypatch.setattr(runner.fs_game_handler, "get_game_root_abs_path", lambda game: root)
    return root


def test_with_no_archive_the_games_own_files_are_the_install(env, monkeypatch, tmp_path):
    _game_folder(env, monkeypatch, tmp_path, {"Metroid.exe": b"x" * 10, "data/level.bin": b"y" * 5})
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid"), None)
    assert env.db.session.state == InstallSessionState.DONE
    assert {e.path: e.size_bytes for e in read_manifest(env.work)} == {"Metroid.exe": 10, "data/level.bin": 5}
    assert env.db.updates[0]["phase_detail"] == "Metroid"


def test_the_games_files_are_linked_not_duplicated_when_they_can_be(env, monkeypatch, tmp_path):
    root = _game_folder(env, monkeypatch, tmp_path, {"Metroid.exe": b"x"})
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid"), None)
    assert (env.work / "Metroid.exe").stat().st_ino == (root / "Metroid.exe").stat().st_ino


def test_the_files_are_copied_when_linking_is_not_possible(env, monkeypatch, tmp_path):
    root = _game_folder(env, monkeypatch, tmp_path, {"Metroid.exe": b"x"})

    def no_link(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(runner.os, "link", no_link)
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid"), None)
    assert (env.work / "Metroid.exe").read_bytes() == b"x"
    assert (env.work / "Metroid.exe").stat().st_ino != (root / "Metroid.exe").stat().st_ino


def test_a_link_in_the_games_folder_is_left_out(env, monkeypatch, tmp_path):
    root = _game_folder(env, monkeypatch, tmp_path, {"Metroid.exe": b"x"})
    outside = tmp_path / "secret.txt"
    outside.write_text("do not serve")
    (root / "leak.txt").symlink_to(outside)
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid"), None)
    assert [e.path for e in read_manifest(env.work)] == ["Metroid.exe"] and not (env.work / "leak.txt").exists()


def test_a_game_that_is_one_file_is_taken_as_that_file(env, monkeypatch, tmp_path):
    single = tmp_path / "library" / "Metroid.exe"
    single.write_bytes(b"MZ")
    monkeypatch.setattr(runner.fs_game_handler, "get_game_root_abs_path", lambda game: single)
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid.exe"), None)
    assert [e.path for e in read_manifest(env.work)] == ["Metroid.exe"]


def test_a_game_whose_files_are_gone_fails_cleanly(env, monkeypatch, tmp_path):
    monkeypatch.setattr(runner.fs_game_handler, "get_game_root_abs_path", lambda game: tmp_path / "nowhere")
    runner._run_extract_only(1, SimpleNamespace(fs_name="Metroid"), None)
    assert env.db.session.state == InstallSessionState.FAILED and "not there" in env.db.session.error


def test_a_missing_source_file_fails_cleanly(env, monkeypatch):
    def gone(game, rel):
        raise FileNotFoundError("Hearthlands.rar is not there")

    monkeypatch.setattr(runner.fs_game_handler, "resolve_installer_abs_path", gone)
    runner._run_extract_only(1, object(), "Hearthlands.rar")
    assert env.db.session.state == InstallSessionState.FAILED and "not there" in env.db.session.error


# --- the start endpoint -----------------------------------------------------------------------------


@pytest.fixture
def start(monkeypatch, tmp_path):
    archive = tmp_path / "Hearthlands.rar"
    archive.write_bytes(b"rar")
    plain = tmp_path / "setup.exe"
    plain.write_bytes(b"MZ")
    made, enqueued = [], []

    class Handler:
        def get_sessions_for_game(self, _id):
            return []

        def add_session(self, session):
            session.id = 7
            session.bytes_written = session.bytes_total = 0  # the column defaults the database would apply
            made.append(session)
            return session

        def update_session(self, _id, fields):
            for key, value in fields.items():
                setattr(made[-1], key, value)
            return made[-1]

        def delete_session(self, _id):
            pass

        def count_running_sessions(self):
            return 0

    paths = {"Hearthlands.rar": archive, "setup.exe": plain}
    monkeypatch.setattr(endpoint, "db_install_session_handler", Handler())
    monkeypatch.setattr(
        endpoint.db_game_handler, "get_game", lambda _id: SimpleNamespace(id=1, name="Hearthlands")
    )
    monkeypatch.setattr(
        endpoint.fs_game_handler, "resolve_installer_abs_path", lambda game, rel: str(paths[rel])
    )
    monkeypatch.setattr(
        endpoint.fs_game_handler,
        "get_installer_candidates",
        lambda game: [cand("Hearthlands.rar", 4, "archive")],
    )
    monkeypatch.setattr(endpoint, "list_source_candidates", lambda path: [cand("setup.exe", 0, "known installer")])
    monkeypatch.setattr(endpoint, "purge_superseded_sessions", lambda *a: None)
    monkeypatch.setattr(endpoint, "enqueue_install", lambda sid: enqueued.append(sid))
    monkeypatch.setattr(endpoint, "default_auto_mode", lambda: False)
    monkeypatch.setattr(endpoint, "default_manual_mode", lambda: False)
    monkeypatch.setattr(endpoint, "resolve_expires_at", lambda ttl: None)

    def go(**form):
        user = SimpleNamespace(id=3)
        return asyncio.run(endpoint.start_install_session(user, 1, InstallStartForm(**form)))

    return SimpleNamespace(go=go, made=made, enqueued=enqueued)


def test_extracting_the_default_archive_as_it_is(start):
    session = start.go(extract_only=True)
    assert (
        session.extract_only is True
        and session.source_path == "Hearthlands.rar"
        and session.installer_path is None
    )
    assert session.state == InstallSessionState.INSTALLING and start.enqueued == [7]


def test_extracting_a_named_archive_given_as_the_installer(start):
    session = start.go(extract_only=True, installer_path="Hearthlands.rar")
    assert (
        session.source_path == "Hearthlands.rar" and session.installer_path is None and session.extract_only
    )


def test_extracting_ignores_manual_mode(start):
    session = start.go(extract_only=True, manual_mode=True)
    assert session.state == InstallSessionState.INSTALLING and start.enqueued == [7]


def test_extracting_a_non_archive_takes_the_games_own_files(start):
    session = start.go(extract_only=True, installer_path="setup.exe")
    assert session.extract_only and session.source_path is None and session.installer_path is None
    assert session.state == InstallSessionState.INSTALLING and start.enqueued == [7]


def test_a_folder_with_no_installer_in_it_is_not_run_until_the_person_picks(start, monkeypatch):
    monkeypatch.setattr(
        endpoint.fs_game_handler, "get_installer_candidates", lambda game: [cand("Metroid.exe", 1, "executable (top level)")]
    )
    session = start.go()
    assert session.state == InstallSessionState.AWAITING_INSTALLER and session.installer_path is None
    assert start.enqueued == []


def test_that_folder_can_still_be_extracted_as_it_is(start, monkeypatch):
    monkeypatch.setattr(
        endpoint.fs_game_handler, "get_installer_candidates", lambda game: [cand("Metroid.exe", 1, "executable (top level)")]
    )
    session = start.go(extract_only=True)
    assert session.extract_only and session.source_path is None and session.installer_path is None
    assert session.state == InstallSessionState.INSTALLING and start.enqueued == [7]


def test_a_chosen_executable_is_run_as_before(start, monkeypatch):
    monkeypatch.setattr(
        endpoint.fs_game_handler, "get_installer_candidates", lambda game: [cand("Metroid.exe", 1, "executable (top level)")]
    )
    session = start.go(installer_path="setup.exe")
    assert session.installer_path == "setup.exe" and not session.extract_only and start.enqueued == [7]


def test_a_folder_with_a_known_installer_is_still_run_by_itself(start, monkeypatch):
    monkeypatch.setattr(
        endpoint.fs_game_handler, "get_installer_candidates", lambda game: [cand("setup.exe", 0, "known installer")]
    )
    session = start.go()
    assert session.installer_path == "setup.exe" and start.enqueued == [7]


def test_a_normal_start_is_not_extract_only(start):
    session = start.go()
    assert session.extract_only is False


# --- the candidates endpoint ------------------------------------------------------------------------


def _candidates_of(monkeypatch, found):
    monkeypatch.setattr(endpoint.db_game_handler, "get_game", lambda _id: SimpleNamespace(id=1, name="Metroid"))
    monkeypatch.setattr(endpoint.fs_game_handler, "get_installer_candidates", lambda game: found)
    return asyncio.run(endpoint.get_install_candidates(SimpleNamespace(id=3), 1))


def test_a_folder_with_only_game_executables_is_suggested_to_be_extracted(monkeypatch):
    answer = _candidates_of(monkeypatch, [cand("Metroid.exe", 1, "executable (top level)")])
    assert answer.extract_suggested is True and [c.path for c in answer.candidates] == ["Metroid.exe"]


def test_a_folder_with_nothing_runnable_is_suggested_to_be_extracted_too(monkeypatch):
    answer = _candidates_of(monkeypatch, [])
    assert answer.extract_suggested is True and answer.needs_manual_pick is True


def test_a_folder_with_an_installer_is_not(monkeypatch):
    answer = _candidates_of(monkeypatch, [cand("setup.exe", 0, "known installer"), cand("game.exe", 1)])
    assert answer.extract_suggested is False


# --- an archive that holds the game itself (Metroid Prime Origins) ----------------------------------


def test_metroid_prime_origins_an_archive_with_the_game_exe_at_its_top_level_needs_no_installer():
    """The zip held MetroidPrimeOrigins.exe next to its data: a top-level executable was taken for the installer,
    so the server ran the game itself in the sandbox instead of keeping the archive's contents as the install."""
    found = [c for c in listing("MetroidPrimeOrigins.exe", "data/rooms.pak", "readme.txt") if c.rank <= 2]
    assert [c.path for c in found] == ["MetroidPrimeOrigins.exe"]
    assert archive_prescan.extract_suggested(found) is True


def test_a_top_level_executable_named_like_an_installer_still_means_there_is_one():
    found = [c for c in listing("Game_Installer.exe", "data.bin") if c.rank <= 2]
    assert archive_prescan.extract_suggested(found) is False


def test_nobody_deciding_an_archive_with_no_installer_is_extracted_not_run(start, monkeypatch):
    monkeypatch.setattr(endpoint, "list_source_candidates", lambda path: [cand("Game.exe", 1, "executable (top level)")])
    session = start.go()
    assert session.extract_only is True and session.source_path == "Hearthlands.rar" and session.installer_path is None
    assert session.state == InstallSessionState.INSTALLING and start.enqueued == [7]


def test_saying_no_to_extracting_runs_the_installer_found_inside_as_before(start, monkeypatch):
    monkeypatch.setattr(endpoint, "list_source_candidates", lambda path: [cand("Game.exe", 1, "executable (top level)")])
    session = start.go(extract_only=False)
    assert session.extract_only is False and session.source_path == "Hearthlands.rar"


def test_an_archive_with_an_installer_inside_is_still_run(start):
    session = start.go()
    assert session.extract_only is False and session.source_path == "Hearthlands.rar"


def test_an_archive_that_cannot_be_listed_is_run_as_before(start, monkeypatch):
    def broken(path):
        raise RuntimeError("7-Zip: cannot open")

    monkeypatch.setattr(endpoint, "list_source_candidates", broken)
    session = start.go()
    assert session.extract_only is False and start.enqueued == [7]


def test_a_plain_executable_is_never_looked_into(start, monkeypatch):
    def looked(path):
        raise AssertionError("not an archive")

    monkeypatch.setattr(endpoint, "list_source_candidates", looked)
    session = start.go(installer_path="setup.exe")
    assert session.extract_only is False and session.installer_path == "setup.exe"

