from pathlib import Path

from handler.install import runner


def test_an_installer_from_a_disc_image_in_the_archive_sees_that_disc_as_d(tmp_path):
    """Regression test: a 1999 CD installer was run from D:\\<image>.bin.extracted\\setup.exe and stopped at "setup.res: file
    not found" (it looks for its files at the root of its drive, as on a real disc). D: is now the image's own folder."""
    disc = tmp_path / "Game.bin.extracted"
    disc.mkdir()
    installer = disc / "setup.exe"
    assert runner._disc_root(tmp_path, installer) == disc
    assert runner._windows_installer_path(installer, tmp_path, disc) == "D:\\setup.exe"


def test_a_nested_folder_inside_the_disc_keeps_its_place_below_d(tmp_path):
    disc = tmp_path / "Game.iso.extracted"
    (disc / "Disc1").mkdir(parents=True)
    installer = disc / "Disc1" / "setup.exe"
    assert runner._disc_root(tmp_path, installer) == disc
    assert runner._windows_installer_path(installer, tmp_path, disc) == "D:\\Disc1\\setup.exe"


def test_an_installer_taken_straight_from_the_archive_keeps_the_whole_extraction_as_d(tmp_path):
    (tmp_path / "Game").mkdir()
    installer = tmp_path / "Game" / "setup.exe"
    assert runner._disc_root(tmp_path, installer) == tmp_path
    assert runner._windows_installer_path(installer, tmp_path, tmp_path) == "D:\\Game\\setup.exe"
    assert runner._windows_installer_path(installer, tmp_path, None) == f"C:\\{runner._SOURCE_LINK_NAME}\\Game\\setup.exe"


def test_a_path_outside_the_extraction_is_left_alone(tmp_path):
    assert runner._disc_root(tmp_path, Path("/elsewhere/setup.exe")) == tmp_path


def test_the_sandbox_starts_a_disc_installer_in_its_own_folder_and_others_in_the_work_dir():
    """Regression test: the CD setup looked for setup.res in its current directory, which was the session's work dir."""
    from handler.install import sandbox

    spec = sandbox.SandboxSpec(
        installer_path="/x/setup.exe", work_dir="/w", proton_prefix="/w/prefix", display=":99", chdir="/x/disc"
    )
    argv = sandbox.build_bwrap_command(spec, ["wine", "D:\\setup.exe"])
    assert argv[argv.index("--chdir") + 1] == "/x/disc"
    plain = sandbox.SandboxSpec(installer_path="/x/setup.exe", work_dir="/w", proton_prefix=None, display=":99")
    argv = sandbox.build_bwrap_command(plain, ["/bin/sh", "/x/setup.sh"])
    assert argv[argv.index("--chdir") + 1] == "/w"
