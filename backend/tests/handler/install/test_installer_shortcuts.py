from handler.install.windows_output import installer_shortcuts


def touch(prefix, *relative):
    path = prefix.joinpath("drive_c", *relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_the_start_menu_desktop_and_proton_shortcuts_of_the_game_are_found_but_not_its_uninstaller(tmp_path):
    menu = ("ProgramData", "Microsoft", "Windows", "Start Menu", "Programs", "Ocean")
    game = touch(tmp_path, *menu, "Barbie Ocean Discovery.lnk")
    desktop = touch(tmp_path, "users", "Public", "Desktop", "Barbie Ocean Discovery.lnk")
    launcher = touch(tmp_path, "proton_shortcuts", "Barbie Ocean Discovery.desktop")
    touch(tmp_path, *menu, "Uninstall Barbie Ocean Discovery.lnk")
    touch(tmp_path, "proton_shortcuts", "Uninstall Barbie Ocean Discovery.desktop")
    touch(tmp_path, "proton_shortcuts", "icons", "16x16", "apps", "abod.png")

    assert sorted(installer_shortcuts(tmp_path)) == sorted([game, desktop, launcher])


def test_an_installer_that_made_no_shortcut_has_none(tmp_path):
    touch(tmp_path, "Program Files (x86)", "Ocean", "level1a.wpk")
    assert installer_shortcuts(tmp_path) == []
    assert installer_shortcuts(tmp_path / "missing") == []
