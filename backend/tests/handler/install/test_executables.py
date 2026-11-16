from pathlib import Path

import pytest

from handler.install.executables import has_game_program, is_program


def make(tmp_path, name, content=b"data"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "Game/game.exe",
        "Game/GAME.EXE",
        "Game/start.bat",
        "Game/run.cmd",
        "Game/dos/GAME.COM",
        "Game/Launch Game.lnk",
        "Game/start.sh",
        "Game/Game.x86_64",
        "Game/Game.x86",
        "Game/Game.AppImage",
        "Game/start.command",
        "Game/game.jar",
        "Game/game.py",
        "Game/Cool.app/Contents/MacOS/Cool",
        "Game/Cool.app/Contents/MacOS/anything-at-all",
    ],
)
def test_a_program_of_any_system_counts(tmp_path, name):
    assert has_game_program([make(tmp_path, name)]) is True


@pytest.mark.parametrize(
    ("content", "what"),
    [
        (b"\x7fELF\x02\x01\x01", "a Linux binary"),
        (b"\xcf\xfa\xed\xfe\x07", "a 64-bit Mach-O"),
        (b"\xfe\xed\xfa\xce", "a 32-bit Mach-O"),
        (b"\xca\xfe\xba\xbe\x00", "a fat Mach-O"),
        (b"#!/bin/sh\necho hi", "a script with no extension"),
    ],
)
def test_a_binary_with_no_extension_counts_by_its_first_bytes(tmp_path, content, what):
    assert has_game_program([make(tmp_path, "Game/bin/Cool", content)]) is True, what


@pytest.mark.parametrize(
    "name",
    [
        "Game/unins000.exe",
        "Game/Uninstall.exe",
        "Game/uninstall-cool.sh",
        "Game/_Redist/dxwebsetup.exe",
        "Game/redist/vc_redist.x64.exe",
        "Game/DirectX/DXSETUP.exe",
        "Game/data.pack",
        "Game/payload.pack2",
        "Game/data-01.bin",
        "Game/readme.txt",
        "Game/lib/libfoo.so",
        "Game/lib/libfoo.so.1.2",
        "Game/lib/libfoo.dylib",
        "Game/Cool_Data/Plugins/x.dll",
        "Game/Foo.class",
    ],
)
def test_what_is_not_the_game_does_not_count(tmp_path, name):
    assert has_game_program([make(tmp_path, name, b"\x7fELF" if "lib" in name or name.endswith(".class") else b"x")]) is False


def test_a_library_whose_name_merely_contains_a_library_word_is_still_a_program():
    assert is_program(Path("Game/Tool.a.exe")) is True


def test_a_plain_data_file_with_no_extension_is_not_a_program(tmp_path):
    assert has_game_program([make(tmp_path, "Game/LICENSE", b"MIT License")]) is False


def test_an_app_folder_outside_its_program_folder_is_not_enough(tmp_path):
    assert has_game_program([make(tmp_path, "Cool.app/Contents/Resources/icon.icns")]) is False


def test_the_payload_a_setup_left_before_unpacking_it_is_not_a_program(tmp_path):
    """Caught live: 3.8 GB payload, an uninstaller and a redistributable's setup, and no game."""
    files = [
        make(tmp_path, "Game/payload.pack2", b"x" * 100),
        make(tmp_path, "Game/unins000.exe"),
        make(tmp_path, "Game/_Redist/dxwebsetup.exe"),
        make(tmp_path, "Game/_Redist/QuickSFV.EXE"),
    ]
    assert has_game_program(files) is False
