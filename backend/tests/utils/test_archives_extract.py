import subprocess
from pathlib import Path

from utils import archives

RAR_FAILURE = (
    "ERROR: Unsupported Method : Game/en.tsv\n"
    "ERROR: Unsupported Method : Game/Game.exe\n\n"
    "Sub items Errors: 334\n\nArchives with Errors: 1\n"
)


def run_returning(monkeypatch, *, out="", err="", code=0, raises=None, writes=()):
    def fake(cmd, **kw):
        assert cmd[:2] == ["7z", "x"] and kw["text"] is True
        for name in writes:
            target = Path(cmd[2][2:]) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
        if raises:
            raise raises
        if code:
            raise subprocess.CalledProcessError(code, cmd, output=out, stderr=err)
        return subprocess.CompletedProcess(cmd, 0, out, err)

    monkeypatch.setattr(archives.subprocess, "run", fake)


def test_a_good_extraction_returns_none(monkeypatch, tmp_path):
    run_returning(monkeypatch, writes=["a/b.txt"])
    assert archives.extract_archive(tmp_path / "x.zip", tmp_path / "out") is None
    assert archives.extract_archive_tree(tmp_path / "x.zip", tmp_path / "out") is True


def test_a_failure_says_what_7z_said(monkeypatch, tmp_path):
    run_returning(
        monkeypatch,
        code=2,
        out="ERROR: Headers Error : x.bin\n\nSub items Errors: 1\n",
        writes=["partial.bin"],
    )
    reason = archives.extract_archive(tmp_path / "x.zip", tmp_path / "out")
    assert reason == "7-Zip: ERROR: Headers Error : x.bin (sub items errors: 1)"
    assert archives.extract_archive_tree(tmp_path / "x.zip", tmp_path / "out") is False


def test_a_rar_that_cannot_be_unpacked_points_at_the_image(monkeypatch, tmp_path):
    run_returning(monkeypatch, code=2, out=RAR_FAILURE)
    reason = archives.extract_archive(tmp_path / "Game.rar", tmp_path / "out")
    assert reason.startswith("7-Zip: ERROR: Unsupported Method : Game/en.tsv (sub items errors: 334)")
    assert "cannot unpack RAR" in reason and "Dockerfile" in reason


def test_the_rar_hint_is_only_for_rar(monkeypatch, tmp_path):
    run_returning(monkeypatch, code=2, out=RAR_FAILURE)
    assert "RAR" not in archives.extract_archive(tmp_path / "Game.7z", tmp_path / "out")


def test_a_timeout_and_a_missing_7z_are_reported(monkeypatch, tmp_path):
    run_returning(monkeypatch, raises=subprocess.TimeoutExpired("7z", 1))
    assert archives.extract_archive(tmp_path / "x.zip", tmp_path / "out") == "extraction timed out"
    run_returning(monkeypatch, raises=FileNotFoundError("7z"))
    assert "7z could not be run" in archives.extract_archive(tmp_path / "x.zip", tmp_path / "out")


def test_an_archive_that_unpacks_to_nothing_is_a_failure(monkeypatch, tmp_path):
    run_returning(monkeypatch)
    assert archives.extract_archive(tmp_path / "x.zip", tmp_path / "out") == "the archive holds no files"


def test_a_failure_with_no_output_still_gives_a_reason(monkeypatch, tmp_path):
    run_returning(monkeypatch, code=7)
    assert archives.extract_archive(tmp_path / "x.zip", tmp_path / "out") == "7-Zip: no output"
