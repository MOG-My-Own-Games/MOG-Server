"""Whether what an installer left holds a program to launch, on any of the systems a game can run on.

An install with no program in it is no install: the setup stopped (or was killed) before it unpacked the game, however many
gigabytes of its payload it had already copied. The check has to be wide, though: a good install wrongly failed is worse than
a bad one let through, so every kind of program of Windows, Linux and macOS counts, and only what is surely not the game
(an uninstaller, a redistributable's own setup, a library) does not.
"""

from __future__ import annotations

from pathlib import Path

# By name. Windows (and DOS): programs, scripts and shortcuts; the Java archive and the scripts that run on every system.
LAUNCHABLE_SUFFIXES = frozenset(
    {
        ".exe", ".bat", ".cmd", ".com", ".lnk", ".ps1", ".vbs",  # Windows, DOS
        ".sh", ".x86", ".x86_64", ".x64", ".appimage", ".out", ".elf",  # Linux (not .bin or .run: data, and self-extracting setups)
        ".command",  # macOS
        ".jar", ".py", ".love",  # any system, with its runtime
    }
)  # fmt: skip

# Libraries and the like: they hold code but are no program, even when their first bytes say so.
LIBRARY_SUFFIXES = frozenset({".so", ".dylib", ".dll", ".a", ".o", ".pyd", ".node", ".ocx", ".class"})

# What is not the game: the uninstaller the installer leaves and the setup of a redistributable it carries along.
NOT_THE_GAME_PREFIXES = ("unins", "uninst", "uninstall")
REDISTRIBUTABLE_FOLDERS = ("_redist", "redist", "commonredist", "vcredist", "directx", "__installer")

# First bytes of a native program: ELF (Linux), Mach-O thin and fat (macOS, both byte orders) and a script with its
# interpreter named. 0xCAFEBABE is a fat Mach-O as well as a Java class file; class files are filtered by name above.
MAGIC = (b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!")
MAGIC_LENGTH = 4
# A bundle's program lives in Contents/MacOS, whatever it is named.
APP_BUNDLE_SUFFIX = ".app"


def _is_excluded(path: Path) -> bool:
    if path.name.lower().startswith(NOT_THE_GAME_PREFIXES):
        return True
    return any(part.lower().startswith(REDISTRIBUTABLE_FOLDERS) for part in path.parts[:-1])


def _is_library(path: Path) -> bool:
    """By its last extension, or a versioned shared object (libfoo.so.1.2): a name like "Tool.a.exe" is no library."""
    name = path.name.lower()
    return path.suffix.lower() in LIBRARY_SUFFIXES or ".so." in name


def _in_app_bundle_program_folder(path: Path) -> bool:
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if part.lower().endswith(APP_BUNDLE_SUFFIX) and parts[i + 1 : i + 3] == ("Contents", "MacOS"):
            return True
    return False


def _looks_like_native_program(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(MAGIC_LENGTH)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in MAGIC)


def is_program(path: Path) -> bool:
    """Whether `path` is a program of the game itself, by what it is called or, for a file with no extension, by its first
    bytes (a Linux or macOS binary, or a script)."""
    if _is_excluded(path) or _is_library(path):
        return False
    if _in_app_bundle_program_folder(path):
        return True
    suffix = path.suffix.lower()
    if suffix in LAUNCHABLE_SUFFIXES:
        return True
    return suffix == "" and _looks_like_native_program(path)


def has_game_program(files: list[Path]) -> bool:
    """Whether any of `files` is a program to launch. The cheap name test goes over every file before the first bytes of any
    are read."""
    if any(
        not _is_excluded(p) and not _is_library(p) and (p.suffix.lower() in LAUNCHABLE_SUFFIXES or _in_app_bundle_program_folder(p))
        for p in files
    ):
        return True
    return any(p.suffix == "" and is_program(p) for p in files)
