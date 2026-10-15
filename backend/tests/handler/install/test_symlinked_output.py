"""An installer is untrusted: a symbolic link it leaves in its output must never make the server read another file."""

import os

from handler.install import windows_output
from handler.install.manifest import hash_files, regular_file_in, scan_live_manifest


def _prefix_with_link(tmp_path):
    secret = tmp_path / "outside" / "secret.db"
    secret.parent.mkdir()
    secret.write_text("password hashes")
    drive_c = tmp_path / "prefix" / "drive_c"
    game = drive_c / "Games" / "Cool"
    game.mkdir(parents=True)
    (game / "real.dat").write_bytes(b"x")
    os.symlink(secret, game / "stolen.dat")
    return tmp_path / "prefix", secret


def test_a_link_in_the_prefix_is_not_collected(tmp_path):
    prefix, _ = _prefix_with_link(tmp_path)
    names = {p.name for p in windows_output.collect_windows_install_files(prefix)}
    assert names == {"real.dat"}


def test_a_link_in_the_work_dir_is_not_collected(tmp_path):
    _, secret = _prefix_with_link(tmp_path)
    work = tmp_path / "work"
    (work / "sub").mkdir(parents=True)
    os.symlink(secret, work / "top.dat")
    os.symlink(secret.parent, work / "linked-dir")
    (work / "sub" / "ok.dat").write_bytes(b"y")
    os.symlink(secret, work / "sub" / "bad.dat")
    assert [p.name for p in windows_output.collect_extra_work_dir_files(work)] == ["ok.dat"]


def test_hashing_and_the_live_manifest_skip_a_link(tmp_path):
    _, secret = _prefix_with_link(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    link = work / "a.dat"
    os.symlink(secret, link)
    assert hash_files([link], root=work) == []
    assert scan_live_manifest(work, [link]) == {}


def test_a_listed_path_is_served_only_when_it_is_a_regular_file_inside_the_cache(tmp_path):
    _, secret = _prefix_with_link(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ok.dat").write_bytes(b"x")
    os.symlink(secret, cache / "link.dat")
    os.symlink(secret.parent, cache / "dir")
    assert regular_file_in(cache, "ok.dat") == cache / "ok.dat"
    assert regular_file_in(cache, "link.dat") is None
    assert regular_file_in(cache, "dir/secret.db") is None
    assert regular_file_in(cache, "../outside/secret.db") is None
    assert regular_file_in(cache, "missing") is None
