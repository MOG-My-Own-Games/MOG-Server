from pathlib import Path

from handler.install.disc_program import copy_missing_disc_files, missing_disc_files
from handler.install.runner import _is_real_disc, _Step


def make(root, *names, size=1):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return root


BARBIE_DISC = (
    "abod.exe", "ocean.exe", "ASSETS/1A.BJW", "ASSETS/Close00.pak", "lang.dat", "Readme.txt", "dsetup.dll",
    "SETUP.EXE", "SETUP.INI", "setup.ins", "_INST32I.EX_", "_ISDEL.EXE", "_SETUP.DLL", "_sys1.cab", "data1.cab",
    "autorun.inf", "directx/dxsetup.exe",
)  # fmt: skip


def installed_of(tmp_path, *names):
    return [p for p in make(tmp_path / "install", *names).rglob("*") if p.is_file()]


def test_a_disc_game_gets_its_program_and_what_it_reads_but_not_the_setups_machinery(tmp_path):
    """Caught live (Barbie Ocean Discovery, a CD game): the setup copied only its data and left abod.exe and the ASSETS folder
    on the disc, so the client found no executable in the install."""
    disc = make(tmp_path / "disc", *BARBIE_DISC)
    installed = installed_of(tmp_path, "Ocean/level1a.wpk")

    missing = {p.as_posix() for p in missing_disc_files(disc, installed)}

    assert missing == {"abod.exe", "ocean.exe", "ASSETS/1A.BJW", "ASSETS/Close00.pak", "lang.dat", "Readme.txt", "dsetup.dll"}


def test_a_file_the_installer_wrote_elsewhere_is_not_missing(tmp_path):
    """The installer put the disc's files in its own folder: they are there, whatever the folder is called."""
    disc = make(tmp_path / "disc", "game.exe", "data/level1.dat", "data/level2.dat", "video/intro.avi")
    installed = installed_of(tmp_path, "Program Files/Game/game.exe", "Program Files/Game/data/level1.dat")

    assert {p.as_posix() for p in missing_disc_files(disc, installed)} == {"data/level2.dat", "video/intro.avi"}


def test_a_file_with_the_name_of_an_installed_one_and_another_size_is_missing(tmp_path):
    disc = make(tmp_path / "disc", "game.exe", "config.ini", size=10)
    make(tmp_path / "install", "Game/game.exe", size=10)
    make(tmp_path / "install", "Game/config.ini", size=3)
    installed = [p for p in (tmp_path / "install").rglob("*") if p.is_file()]

    assert {p.as_posix() for p in missing_disc_files(disc, installed)} == {"config.ini"}


def test_a_file_named_like_a_setup_file_in_a_data_folder_is_kept(tmp_path):
    disc = make(tmp_path / "disc", "game.exe", "data/setup.dat", "data/_cache.bin")

    assert {p.as_posix() for p in missing_disc_files(disc, [])} == {"game.exe", "data/setup.dat", "data/_cache.bin"}


def test_without_a_program_anywhere_nothing_is_added(tmp_path):
    disc = make(tmp_path / "disc", "SETUP.EXE", "data1.cab", "ASSETS/1A.BJW")

    assert missing_disc_files(disc, installed_of(tmp_path, "Ocean/level1a.wpk")) == []


def test_an_install_with_its_program_still_takes_the_data_the_disc_keeps(tmp_path):
    disc = make(tmp_path / "disc", "game.exe", "video/intro.avi")
    installed = installed_of(tmp_path, "Game/game.exe")

    assert [p.as_posix() for p in missing_disc_files(disc, installed)] == ["video/intro.avi"]


def test_the_files_are_copied_beside_the_installed_ones_and_never_over_them(tmp_path):
    disc = make(tmp_path / "disc", *BARBIE_DISC)
    install = tmp_path / "install"
    make(install, "Ocean/level1a.wpk")
    (install / "lang.dat").write_bytes(b"installed, another size")
    installed = [p for p in install.rglob("*") if p.is_file()]

    added = copy_missing_disc_files(disc, install, installed)

    assert (install / "abod.exe").is_file() and (install / "ASSETS/1A.BJW").is_file()
    assert (install / "lang.dat").read_bytes() == b"installed, another size"
    assert install / "lang.dat" not in added and install / "abod.exe" in added
    assert not (install / "SETUP.EXE").exists() and not (install / "data1.cab").exists()


def test_a_link_in_the_disc_is_not_followed(tmp_path):
    """The sandbox links a Games folder into the disc, to the writable /Games: whatever is there is not the disc's."""
    outside = make(tmp_path / "outside", "secret.exe")
    disc = make(tmp_path / "disc", "game.exe")
    (disc / "Games").symlink_to(outside)
    (disc / "link.exe").symlink_to(outside / "secret.exe")

    assert {p.as_posix() for p in missing_disc_files(disc, [])} == {"game.exe"}


def step(disc_source, source_abs):
    return _Step("setup.exe", source_abs, Path("/x"), disc_source, None)


def test_only_an_unpacked_disc_image_is_a_disc(tmp_path):
    """The unpacked archive of a distributor is not a disc: its payload, which the installer did not copy, is no part of the game."""
    assert _is_real_disc(step(Path("/tmp/x"), Path("/library/g/Game.iso")))
    assert _is_real_disc(step(Path("/tmp/x/Disc1.extracted"), Path("/library/g/Game.zip")))
    assert not _is_real_disc(step(Path("/tmp/x"), Path("/library/g/Game.zip")))
    assert not _is_real_disc(step(None, None))
