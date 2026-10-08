from handler.filesystem.installer_detection import (
    DetectedFile,
    detect_installer_candidates,
    pick_default_installer,
)


class TestDetectInstallerCandidates:
    def test_known_installer_name_wins_over_size(self):
        files = [
            DetectedFile(path="setup.exe", size_bytes=1_000_000),
            DetectedFile(path="data/bigfile.bin", size_bytes=50_000_000),
        ]
        candidates = detect_installer_candidates(files)
        assert candidates[0].path == "setup.exe"
        assert candidates[0].kind == "known installer"

    def test_top_level_executable_ranks_above_nested(self):
        # Neither name matches KNOWN_INSTALLER_PATTERNS, so this isolates
        # top-level-vs-nested ranking from the known-name check.
        files = [
            DetectedFile(path="subdir/launcher.exe", size_bytes=1000),
            DetectedFile(path="game.exe", size_bytes=1000),
        ]
        candidates = detect_installer_candidates(files)
        assert candidates[0].path == "game.exe"
        assert candidates[0].kind == "executable (top level)"

    def test_disc_image_ranks_below_any_executable(self):
        files = [
            DetectedFile(path="game.iso", size_bytes=4_000_000_000),
            DetectedFile(path="readme.exe", size_bytes=100),
        ]
        candidates = detect_installer_candidates(files)
        assert candidates[0].path == "readme.exe"

    def test_bundled_redistributable_is_excluded(self):
        """A VC++/DirectX/.NET redistributable bundled alongside the real
        installer must never be picked as *the* installer - running it does
        nothing for the game itself (caught live: Hearthlands' own
        .../Steamworks Shared/_CommonRedist/vcredist/2010/vcredist_x64.exe,
        at 14MB, outranked the real 1MB Hearthlands.exe purely because
        same-rank ties break by descending file size)."""
        files = [
            DetectedFile(path="Hearthlands.exe", size_bytes=1_000_000),
            DetectedFile(
                path="Steamworks Shared/_CommonRedist/vcredist/2010/vcredist_x64.exe",
                size_bytes=14_000_000,
            ),
        ]
        candidates = detect_installer_candidates(files)
        assert len(candidates) == 1
        assert candidates[0].path == "Hearthlands.exe"

    def test_redistributable_dir_match_is_case_insensitive(self):
        files = [DetectedFile(path="Game/REDIST/dxsetup.exe", size_bytes=5000)]
        assert detect_installer_candidates(files) == []

    def test_no_candidates_returns_empty_list(self):
        files = [DetectedFile(path="readme.txt", size_bytes=100)]
        assert detect_installer_candidates(files) == []


class TestPickDefaultInstaller:
    def test_returns_top_ranked(self):
        candidates = detect_installer_candidates(
            [
                DetectedFile(path="setup.exe", size_bytes=100),
                DetectedFile(path="archive.zip", size_bytes=100),
            ]
        )
        assert pick_default_installer(candidates).path == "setup.exe"

    def test_empty_list_returns_none(self):
        assert pick_default_installer([]) is None


class TestCategoryFolders:
    def test_addon_folders_tag_candidates_and_rank_after_the_game(self):
        files = [
            DetectedFile(path="dlc/setup.exe", size_bytes=9_000_000),
            DetectedFile(path="Mods/pack.exe", size_bytes=9_000_000),
            DetectedFile(path="setup.exe", size_bytes=1_000),
        ]
        candidates = detect_installer_candidates(files)
        assert [(c.path, c.category) for c in candidates] == [
            ("setup.exe", "game"),
            ("dlc/setup.exe", "dlc"),
            ("Mods/pack.exe", "mod"),
        ]

    def test_non_installable_folders_are_skipped(self):
        files = [DetectedFile(path="soundtrack/play.exe", size_bytes=1), DetectedFile(path="manuals/a.exe", size_bytes=1)]
        assert detect_installer_candidates(files) == []

    def test_only_the_top_folder_counts(self):
        files = [DetectedFile(path="game/dlc/setup.exe", size_bytes=1)]
        assert detect_installer_candidates(files)[0].category == "game"

    def test_addon_is_never_the_default_pick(self):
        files = [DetectedFile(path="dlc/setup.exe", size_bytes=1)]
        assert pick_default_installer(detect_installer_candidates(files)) is None


def _found(*paths):
    from handler.filesystem.installer_detection import DetectedFile, detect_installer_candidates

    return detect_installer_candidates([DetectedFile(p, 1000) for p in paths])


class TestLooksPortable:
    """A folder with no installer in it (the Metroid case) is offered its executables and "just extract"."""

    def test_only_game_executables_is_portable(self):
        from handler.filesystem.installer_detection import looks_portable

        assert looks_portable(_found("Metroid.exe", "Metroid/bin/helper.exe", "readme.txt"))

    def test_nothing_runnable_is_portable(self):
        from handler.filesystem.installer_detection import looks_portable

        assert looks_portable(_found("data.pak", "readme.txt")) and looks_portable([])

    def test_known_installer_names_are_not(self):
        from handler.filesystem.installer_detection import looks_portable

        for name in ("setup.exe", "gog-game.exe", "setup_game_1.0.exe", "install.exe"):
            assert not looks_portable(_found(name, "game.exe")), name

    def test_a_name_with_setup_or_install_in_it_is_an_installer_but_an_uninstaller_is_not(self):
        from handler.filesystem.installer_detection import looks_portable

        assert not looks_portable(_found("Game_Installer.exe"))
        assert not looks_portable(_found("installgame.exe"))
        assert looks_portable(_found("uninstall.exe", "game.exe"))

    def test_linux_installers_archives_and_disc_images_are_not(self):
        from handler.filesystem.installer_detection import looks_portable

        from handler.filesystem.installer_detection import DetectedFile, detect_installer_candidates

        for name in ("game.run", "Game.AppImage", "Game.zip", "Game.iso"):
            assert not looks_portable(_found(name)), name
        big_script = detect_installer_candidates([DetectedFile("game_1_2_3.sh", 400 * 1024**2)])
        assert not looks_portable(big_script)  # a GOG Linux installer carries the game

    def test_a_games_own_start_script_is_not_an_installer(self):
        from handler.filesystem.installer_detection import looks_portable

        # What a Ren'Py zip holds: the game's exe and .sh, and DirectX's web installer in lib/.
        files = _found(
            "Some_Game_1.0-pc/Some_Game.exe",
            "Some_Game_1.0-pc/Some_Game.sh",
            "Some_Game_1.0-pc/lib/windows-i686/dxwebsetup.exe",
            "Some_Game_1.0-pc/lib/windows-i686/python.exe",
        )
        assert looks_portable(files)
        assert [c.file_name for c in files] == ["Some_Game.exe", "python.exe"]  # no .sh, no DirectX installer

    def test_prerequisite_installers_are_told_by_name_wherever_they_are(self):
        for name in ("dxwebsetup.exe", "DXSETUP.exe", "dx9setup.exe", "dx_setup.exe", "DXSetup_x64.exe", "dx-web-setup.exe", "vcredist_x64.exe", "VC_redist.x86.exe", "dotnetfx35.exe", "oalinst.exe", "PhysX_9.exe"):
            assert _found(f"game/lib/{name}") == [], name
        assert [c.file_name for c in _found("game/setup.exe")] == ["setup.exe"]  # an installer of the game stays
        assert [c.file_name for c in _found("game/dxcpl.exe", "game/dxdiag_tool.exe")] == ["dxcpl.exe", "dxdiag_tool.exe"]  # not every dx name

    def test_a_dlc_installer_does_not_make_the_base_game_one(self):
        from handler.filesystem.installer_detection import looks_portable

        assert looks_portable(_found("Metroid.exe", "DLC/setup_dlc.exe"))

    def test_the_bundled_prerequisites_do_not_count(self):
        from handler.filesystem.installer_detection import looks_portable

        assert looks_portable(_found("Metroid.exe", "_CommonRedist/vcredist/setup.exe"))
