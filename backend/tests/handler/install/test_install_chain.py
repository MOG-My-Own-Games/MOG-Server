from pathlib import Path
from types import SimpleNamespace

import pytest

from handler.install import runner
from models.install_session import InstallSession, InstallSessionState


class FakeDb:
    def __init__(self, steps):
        self.session = SimpleNamespace(
            id=1,
            game_id=3,
            state=InstallSessionState.INSTALLING,
            installer_path=steps[0][0],
            source_path=steps[0][1],
            extract_only=False,
            steps=[{"path": p, "source_path": s} for p, s in steps],
            error=None,
            step_index=0,
            proton_build=None,
            auto_mode=False,
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
def chain(monkeypatch, tmp_path):
    """A runner with the sandbox, Wine and VNC replaced: every installer run is recorded and its outcome scripted."""
    library = tmp_path / "library" / "Game"
    library.mkdir(parents=True)
    work = tmp_path / "cache" / "1"
    work.mkdir(parents=True)
    names = {}
    for name in ("setup.exe", "patch1.exe", "patch2.exe", "lost.sh"):
        (library / name).write_bytes(b"x" * 10)
        names[name] = str(library / name)

    run = SimpleNamespace(
        installers=[],  # the installer each run started
        outcomes={},  # installer name -> (exit code, timed out), default (0, False)
        finalized=[],
        streamed=0,
        vnc_started=0,
        vnc_stopped=0,
        bytes=0,
    )

    class Vnc:
        display, public_url, web_port, token = ":99", "/vnc", 6080, "t"

        def stop(self):
            run.vnc_stopped += 1

    def start_vnc(*a, **k):
        run.vnc_started += 1
        return Vnc()

    def run_installer(argv, display, auto_mode_session=None):
        run.installers.append(argv)
        outcome = run.outcomes.get(argv[-1].rsplit("/", 1)[-1], (0, False))
        if outcome[1]:
            raise runner.subprocess.TimeoutExpired(argv, 1)
        return outcome[0]

    def build_inner(installer_arg, proton_or_wine):
        return ["run", installer_arg]

    def wrap(inner, **kwargs):
        return [*inner, kwargs["installer_abs"]]

    db = FakeDb([("setup.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)
    monkeypatch.setattr(runner.db_game_handler, "get_game", lambda _id: SimpleNamespace(id=3, size_bytes=None))
    monkeypatch.setattr(runner.fs_game_handler, "resolve_installer_abs_path", lambda game, rel: names[rel])
    monkeypatch.setattr(runner.fs_game_handler, "get_game_root_abs_path", lambda game: library)
    monkeypatch.setattr(runner, "ensure_session_cache_dir", lambda _id: work)
    monkeypatch.setattr(runner, "start_vnc_session", start_vnc)
    monkeypatch.setattr(runner, "_run_installer", run_installer)
    monkeypatch.setattr(runner, "_uses_wine", lambda path: not path.endswith(".sh"))
    monkeypatch.setattr(runner, "_wine_or_proton", lambda build=None: "wine")
    monkeypatch.setattr(runner, "_is_proton", lambda name: False)
    monkeypatch.setattr(runner, "default_build_id", lambda: None)
    monkeypatch.setattr(runner, "_init_wine_prefix", lambda *a, **k: None)
    monkeypatch.setattr(runner, "load_baseline", lambda prefix: frozenset())
    monkeypatch.setattr(runner, "_configure_drive_letters", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_windows_installer_path", lambda abs_path, root, disc: str(abs_path))
    monkeypatch.setattr(runner, "_build_inner_command", build_inner)
    monkeypatch.setattr(runner, "_wrap_for_sandbox", wrap)
    monkeypatch.setattr(runner, "_live_manifest_loop", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_install_output_looks_finished", lambda work_dir: False)
    monkeypatch.setattr(runner, "_native_env", lambda work_dir: ())
    monkeypatch.setattr(runner, "notify_auto_mode_failed", lambda *a: None)
    monkeypatch.setattr(runner, "_set_phase", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_route_disc_games_dir", lambda *a: None)

    def finalize(install_session_id, prefix_dir, work_dir, baseline=frozenset(), exit_code=None, installer_bytes=None):
        run.finalized.append(installer_bytes)

    def stream(install_session_id, vnc):
        run.streamed += 1
        vnc.stop()

    monkeypatch.setattr(runner, "_finalize_install", finalize)
    monkeypatch.setattr(runner, "_enter_streaming", stream)
    return run


def test_the_steps_run_in_order_inside_one_session_and_are_finalized_once(chain, monkeypatch):
    db = FakeDb([("setup.exe", None), ("patch1.exe", None), ("patch2.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)

    assert runner._run_install(1) is False

    assert [argv[-1].rsplit("/", 1)[-1] for argv in chain.installers] == ["setup.exe", "patch1.exe", "patch2.exe"]
    assert chain.vnc_started == 1 and chain.streamed == 1 and chain.vnc_stopped >= 1
    assert len(chain.finalized) == 1
    assert {"step_index": 2} in db.updates
    assert db.session.step_index == 2


def test_a_step_that_times_out_without_a_finished_install_stops_the_chain_and_names_the_step(chain, monkeypatch):
    db = FakeDb([("setup.exe", None), ("patch1.exe", None), ("patch2.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)
    chain.outcomes["patch1.exe"] = (0, True)

    runner._run_install(1)

    assert db.session.state == InstallSessionState.FAILED
    assert db.session.error == "Step 2/3: Installer timed out"
    assert [argv[-1].rsplit("/", 1)[-1] for argv in chain.installers] == ["setup.exe", "patch1.exe"]
    assert chain.finalized == []


def test_a_single_installer_keeps_its_plain_error(chain, monkeypatch):
    db = FakeDb([("setup.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)
    chain.outcomes["setup.exe"] = (0, True)

    runner._run_install(1)

    assert db.session.error == "Installer timed out"


def test_a_chain_cannot_mix_windows_and_native_installers(chain, monkeypatch):
    db = FakeDb([("setup.exe", None), ("lost.sh", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)

    runner._run_install(1)

    assert db.session.state == InstallSessionState.FAILED
    assert db.session.error.startswith("Step 2/2: it is a native installer")
    # The first installer ran before the native one was found out of place: the session stops there.
    assert [argv[-1].rsplit("/", 1)[-1] for argv in chain.installers] == ["setup.exe"]


def test_the_installed_size_of_a_chain_is_the_sum_of_its_installers(chain, monkeypatch):
    monkeypatch.setattr(runner, "_installer_bytes", lambda game, source: 10)
    db = FakeDb([("setup.exe", None), ("patch1.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)

    runner._run_install(1)

    assert chain.finalized == [20]


def test_steps_of_a_chain_are_read_in_their_order_and_a_single_installer_is_its_own_pair():
    chain_session = InstallSession(
        steps=[{"path": "setup.exe", "source_path": None}, {"path": "Patch/inner.exe", "source_path": "patch.zip"}],
        installer_path="setup.exe",
    )
    single = InstallSession(installer_path="setup.exe", source_path=None, steps=None)

    assert runner._steps_of(chain_session) == [("setup.exe", None), ("Patch/inner.exe", "patch.zip")]
    assert runner._steps_of(single) == [("setup.exe", None)]


def test_a_failed_step_is_labelled_only_in_a_chain():
    assert runner._step_label(0, 1, "x") == "x"
    assert runner._step_label(1, 3, "x") == "Step 2/3: x"


def test_a_cancel_during_an_installer_keeps_the_cancel_reason_and_finalizes_nothing(chain, monkeypatch):
    """The endpoint marks a cancelled session at once and kills the installer. The runner must not then go on to
    finalize what the killed installer left, nor replace the cancel reason with an error about missing files."""
    db = FakeDb([("setup.exe", None), ("patch1.exe", None)])
    monkeypatch.setattr(runner, "db_install_session_handler", db)
    real_run = runner._run_installer

    def cancelled_while_running(argv, display, auto_mode_session=None):
        db.session.state = InstallSessionState.FAILED
        db.session.error = runner.CANCELLED_ERROR
        real_run(argv, display, auto_mode_session)
        return -9

    monkeypatch.setattr(runner, "_run_installer", cancelled_while_running)

    runner._run_install(1)

    assert db.session.error == runner.CANCELLED_ERROR and db.session.state == InstallSessionState.FAILED
    assert chain.finalized == []
    assert len(chain.installers) == 1
