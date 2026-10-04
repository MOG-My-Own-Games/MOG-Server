from pathlib import Path

from handler.install.windows_output import _is_blacklisted, collect_windows_install_files, resolve_install_root


def test_installer_shortcuts_are_not_install_content():
    assert _is_blacklisted(("users", "Public", "Desktop", "Jazz Jackrabbit 2.lnk"))
    assert _is_blacklisted(("Users", "steamuser", "Start Menu", "Programs", "x.lnk"))
    assert not _is_blacklisted(("users", "steamuser", "Documents", "My Games", "save.dat"))
    assert not _is_blacklisted(("GOG Games", "Game", "desktop.ini"))


def test_a_late_shortcut_does_not_move_the_root_from_the_games_folder_to_drive_c(tmp_path: Path):
    prefix = tmp_path / "prefix"
    drive_c = prefix / "drive_c"
    game = drive_c / "GOG Games" / "Game"
    game.mkdir(parents=True)
    (game / "game.exe").write_bytes(b"x")

    early = collect_windows_install_files(prefix)
    root_while_installing = resolve_install_root(drive_c, early)

    shortcut = drive_c / "users" / "Public" / "Desktop" / "Game.lnk"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_bytes(b"lnk")

    late = collect_windows_install_files(prefix)
    assert resolve_install_root(drive_c, late) == root_while_installing == drive_c / "GOG Games"
    assert shortcut not in late
