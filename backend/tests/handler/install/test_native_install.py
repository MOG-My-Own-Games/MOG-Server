from pathlib import Path
from types import SimpleNamespace

import pytest
from handler.install import runner, sandbox
from handler.install.manifest import read_manifest
from models.install_session import InstallSessionState


class FakeDb:
    def __init__(self, **session):
        self.session = SimpleNamespace(
            id=1,
            game_id=3,
            state=InstallSessionState.INSTALLING,
            installer_path="lost_ruins.sh",
            source_path=None,
            extract_only=False,
            proton_build=None,
            **session,
        )
        self.updates = []

    def get_session(self, _id):
        return self.session

    def update_session(self, _id, fields):
        self.updates.append(dict(fields))
        for key, value in fields.items():
            setattr(self.session, key, value)
        return self.session


@pytest.fixture
def native(monkeypatch, tmp_path):
    db = FakeDb()
    library = tmp_path / "library"
    library.mkdir()
    installer = library / "lost_ruins.sh"
    installer.write_text("#!/bin/sh\n")
    work = tmp_path / "cache" / "1"
    work.mkdir(parents=True)
    ran = {}

    def forbidden(*a, **k):
        raise AssertionError("a native installer must not touch Proton or Wine")

    class Vnc:
        display, public_url, web_port, token = ":99", "/vnc", 6080, "t"

        def stop(self):
            ran["vnc_stopped"] = True

    def run_installer(argv, display, auto_mode_session=None):
        ran["argv"] = argv
        flat = argv
        home = Path(next(flat[i + 2] for i in range(len(flat)) if flat[i : i + 2] == ["--setenv", "HOME"]))
        game = home / "GOG Games" / "Lost Ruins"
        (game / "bin").mkdir(parents=True)
        (game / "Lost Ruins").write_bytes(b"x" * 100)
        (game / "bin" / "data.pak").write_bytes(b"y" * 50)
        (game / "start.sh").write_text("source support/gog_com.shlib\n")
        (game / "gameinfo").write_text("Lost Ruins\n")
        (game / "support").mkdir()
        (game / "support" / "gog_com.shlib").write_text("lib")
        (game / "support" / "yad").mkdir()
        (game / ".mojosetup" / "meta").mkdir(parents=True)
        (game / ".mojosetup" / "mojosetup").write_bytes(b"m" * 7)
        (game / ".mojosetup" / "meta" / "x").write_text("m")
        (game / "uninstall-Lost Ruins.sh").write_text("exec .mojosetup/mojosetup uninstall")
        (home / ".config").mkdir()
        (home / ".config" / "mojosetup.cfg").write_text("noise")
        (home / ".local" / "share" / "applications").mkdir(parents=True)
        (home / ".local" / "share" / "applications" / "lost-ruins.desktop").write_text("noise")
        (home / "Desktop").mkdir()
        (home / "Desktop" / "Lost Ruins.desktop").write_text("noise")
        tmp = Path(next(flat[i + 2] for i in range(len(flat)) if flat[i : i + 2] == ["--setenv", "TMPDIR"]))
        (tmp / "selfgz1234").mkdir()
        (tmp / "selfgz1234" / "mojosetup").write_bytes(b"z" * 10)

    monkeypatch.setattr(runner, "db_install_session_handler", db)
    monkeypatch.setattr(runner.db_game_handler, "get_game", lambda _id: SimpleNamespace(id=3))
    monkeypatch.setattr(
        runner.fs_game_handler, "resolve_installer_abs_path", lambda game, rel: str(installer)
    )
    monkeypatch.setattr(runner.fs_game_handler, "get_game_root_abs_path", lambda game: library)
    monkeypatch.setattr(runner, "ensure_session_cache_dir", lambda _id: work)
    started = []
    monkeypatch.setattr(runner, "start_vnc_session", lambda *a, **k: started.append(a) or Vnc())
    monkeypatch.setattr(runner, "_run_installer", run_installer)
    monkeypatch.setattr(runner, "notify_auto_mode_failed", lambda *a: None)
    monkeypatch.setattr(runner, "INSTALL_SANDBOX_ENABLED", True)
    for name in (
        "_wine_or_proton",
        "_init_wine_prefix",
        "_configure_drive_letters",
        "resolve_proton_path",
        "default_build_id",
    ):
        monkeypatch.setattr(runner, name, forbidden)
    return SimpleNamespace(db=db, work=work, installer=installer, ran=ran, started=started)


def test_a_native_installer_runs_without_proton_or_a_prefix(native):
    runner._run_install(1)
    argv = native.ran["argv"]
    assert argv[0] == "bwrap" and argv[-2:] == ["/bin/sh", str(native.installer)]
    assert "WINEPREFIX" not in argv and not any("STEAM_COMPAT" in a for a in argv)
    assert not (native.work / "prefix").exists() and not (native.work / "steam-client").exists()


def test_its_home_and_temporary_files_are_inside_the_session_cache(native):
    runner._run_install(1)
    argv = native.ran["argv"]
    home = argv[argv.index("HOME") + 1]
    tmp = argv[argv.index("TMPDIR") + 1]
    assert home == str(native.work / "home") and tmp == str(native.work / "tmp")


def test_the_game_folder_becomes_the_install_and_the_noise_is_left_out(native):
    runner._run_install(1)
    assert native.db.session.state == InstallSessionState.DONE
    manifest = {e.path: e.size_bytes for e in read_manifest(native.work)}
    assert manifest == {  # from "GOG Games" up
        "Lost Ruins/Lost Ruins": 100,
        "Lost Ruins/bin/data.pak": 50,
        "Lost Ruins/start.sh": 29,
        "Lost Ruins/gameinfo": 11,
        "Lost Ruins/support/gog_com.shlib": 3,  # start.sh sources it: the support folder stays
    }
    assert (native.work / "Lost Ruins" / "bin" / "data.pak").is_file()
    assert not (native.work / "home").exists() and not (native.work / "tmp").exists()
    assert native.db.session.bytes_total == 193 and native.ran["vnc_stopped"] is True


def test_an_installer_that_wrote_nothing_fails_the_session(native, monkeypatch):
    monkeypatch.setattr(runner, "_run_installer", lambda *a, **k: None)
    runner._run_install(1)
    assert native.db.session.state == InstallSessionState.FAILED
    assert "wrote no files" in native.db.session.error


def test_only_hidden_folders_and_shortcuts_are_noise(tmp_path):
    for noisy in (".config/x", ".local/share/y", ".cache/z", "Desktop/a.desktop"):
        assert runner._is_user_noise(Path(noisy))
    for game in ("GOG Games/Lost Ruins/bin", "Games/x", "Lost Ruins/x"):
        assert not runner._is_user_noise(Path(game))


def test_files_that_are_not_under_gog_games_keep_their_own_top_level(tmp_path, monkeypatch):
    work = tmp_path / "w"
    (work / "home" / "Games" / "Cool").mkdir(parents=True)
    (work / "home" / "Games" / "Cool" / "run").write_bytes(b"x")
    (work / "home" / "Other").mkdir()
    (work / "home" / "Other" / "f").write_bytes(b"y")
    monkeypatch.setattr(runner, "db_install_session_handler", FakeDb())
    runner._finalize_native_install(1, work)
    assert {e.path for e in read_manifest(work)} == {"Games/Cool/run", "Other/f"}


def test_the_sandbox_has_no_prefix_for_a_native_installer():
    spec = sandbox.SandboxSpec(installer_path="/lib/x.sh", work_dir="/w", proton_prefix=None, display=":99")
    argv = sandbox.build_bwrap_command(spec, ["/bin/sh", "/lib/x.sh"])
    assert "WINEPREFIX" not in argv and argv.count("/w") >= 2  # bound and the working directory


def test_the_sandbox_still_binds_the_prefix_for_wine():
    spec = sandbox.SandboxSpec(
        installer_path="/lib/x.exe", work_dir="/w", proton_prefix="/w/prefix", display=":99"
    )
    argv = sandbox.build_bwrap_command(spec, ["wine", "/lib/x.exe"])
    assert "/w/prefix" in argv and argv[argv.index("WINEPREFIX") + 1] == "/w/prefix"


def test_a_native_installer_gets_a_taller_screen_than_a_windows_wizard(native):
    runner._run_install(1)
    ((session_id, web_root, resolution),) = native.started
    assert resolution == runner.INSTALL_NATIVE_VNC_RESOLUTION
    width, height, depth = (int(part) for part in resolution.split("x"))
    assert height > 600 and height > int(runner.INSTALL_VNC_RESOLUTION.split("x")[1])
    assert runner.INSTALL_VNC_RESOLUTION == "800x600x24"  # a Windows wizard keeps its own


def test_the_screen_sizes_come_from_the_environment_when_they_are_sound(monkeypatch):
    import config

    monkeypatch.setenv("SIZE_OK", "1280x1024x24")
    monkeypatch.setenv("SIZE_BAD", "huge")
    monkeypatch.setenv("SIZE_TINY", "100x100x24")
    monkeypatch.setenv("SIZE_DEPTH", "1024x768x7")
    assert config._resolution("SIZE_OK", "800x600x24") == "1280x1024x24"
    for key in ("SIZE_BAD", "SIZE_TINY", "SIZE_DEPTH", "SIZE_UNSET"):
        assert config._resolution(key, "800x600x24") == "800x600x24"


def test_mojosetups_own_folder_and_its_uninstaller_are_not_part_of_the_game(tmp_path):
    game = tmp_path / "Lost Ruins"
    (game / ".mojosetup" / "meta").mkdir(parents=True)
    (game / "support").mkdir()
    for name in (
        "uninstall-Lost Ruins.sh",
        "start.sh",
        "support/gog_com.shlib",
        ".mojosetup/mojosetup",
        ".mojosetup/meta/x",
    ):
        (game / name).write_text("x")
    keep = [
        str(p.relative_to(game))
        for p in sorted(game.rglob("*"))
        if p.is_file() and not runner._is_installer_leftover(p)
    ]
    assert keep == ["start.sh", "support/gog_com.shlib"]


def test_an_uninstall_script_with_no_mojosetup_beside_it_is_kept(tmp_path):
    (tmp_path / "uninstall-notes.sh").write_text("a game's own script")
    assert not runner._is_installer_leftover(tmp_path / "uninstall-notes.sh")
