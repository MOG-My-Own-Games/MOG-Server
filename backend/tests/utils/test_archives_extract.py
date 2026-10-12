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


# --- a CD ripped sector by sector -------------------------------------------------------------------

SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def raw_sector(data: bytes, mode: int = 1) -> bytes:
    """A 2352-byte sector: sync, a 4-byte header, (mode 2: 8 bytes of subheader,) the 2048 data bytes, then error codes."""
    head = SYNC + b"\x00\x02\x00" + bytes([mode]) + (b"\x00" * 8 if mode == 2 else b"")
    return (head + data.ljust(2048, b"\x00")).ljust(2352, b"\x00")


def test_the_data_of_a_raw_mode_1_image_is_copied_without_the_framing(tmp_path):
    image = tmp_path / "Game.img"
    image.write_bytes(raw_sector(b"one") + raw_sector(b"two"))
    assert archives.raw_sector_data_offset(image) == 16
    iso = tmp_path / "Game.iso"
    assert archives.raw_sectors_to_iso(image, iso, 16) is True
    assert iso.read_bytes() == b"one".ljust(2048, b"\x00") + b"two".ljust(2048, b"\x00")


def test_a_mode_2_image_skips_the_subheader_and_audio_after_the_data_is_left_out(tmp_path):
    image = tmp_path / "Game.bin"
    image.write_bytes(raw_sector(b"one", 2) + raw_sector(b"two", 2) + b"\x07" * 2352)  # a track of audio follows
    assert archives.raw_sector_data_offset(image) == 24
    iso = tmp_path / "Game.iso"
    assert archives.raw_sectors_to_iso(image, iso, 24) is True
    assert iso.read_bytes() == b"one".ljust(2048, b"\x00") + b"two".ljust(2048, b"\x00")


def test_other_files_are_not_taken_for_raw_images(tmp_path):
    plain = tmp_path / "Game.img"
    plain.write_bytes(b"\x00" * 4096)
    assert archives.raw_sector_data_offset(plain) is None
    zipped = tmp_path / "Game.zip"
    zipped.write_bytes(raw_sector(b"x"))
    assert archives.raw_sector_data_offset(zipped) is None


def test_a_raw_image_7z_cannot_open_is_unpacked_through_a_plain_copy(monkeypatch, tmp_path):
    """Regression test: a CloneCD .img (2352-byte sectors) failed with "Cannot open the file as archive" and the game could
    not be installed or extracted; its data goes through a copy without the sector framing, which 7z reads."""
    image = tmp_path / "library" / "Game.img"
    image.parent.mkdir()
    image.write_bytes(raw_sector(b"one"))
    opened = []

    def fake(cmd, **kw):
        source = Path(cmd[-1])
        opened.append((source.suffix, source.stat().st_size))
        if source.suffix == ".img":
            raise subprocess.CalledProcessError(2, cmd, output="ERROR: Cannot open the file as archive", stderr="")
        (Path(cmd[2][2:]) / "setup.exe").write_bytes(b"MZ")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(archives.subprocess, "run", fake)
    assert archives.extract_archive(image, tmp_path / "cache" / "1") is None
    assert opened == [(".img", 2352), (".iso", 2048)]
    assert (tmp_path / "cache" / "1" / "setup.exe").is_file()
    assert not list((tmp_path / "cache").glob("mog-raw-iso-*"))  # the copy is gone


def test_a_failure_on_a_file_that_is_not_a_raw_image_is_reported_as_before(monkeypatch, tmp_path):
    image = tmp_path / "Game.img"
    image.write_bytes(b"\x00" * 4096)
    run_returning(monkeypatch, code=2, out="ERROR: Cannot open the file as archive\n")
    assert "Cannot open the file as archive" in archives.extract_archive(image, tmp_path / "out")


def test_a_raw_image_is_listed_through_a_plain_copy(monkeypatch, tmp_path):
    """Regression test: a raw CD image (CloneCD .img) holding a setup.exe listed as empty, so the game looked like one with no
    installer and was offered (and installed) as "extract as it is"."""
    image = tmp_path / "Game.img"
    image.write_bytes(raw_sector(b"one"))

    def fake(cmd, **kw):
        source = Path(cmd[-1])
        if source.suffix == ".img":
            raise subprocess.CalledProcessError(2, cmd, output="ERROR: Cannot open the file as archive", stderr="")
        return subprocess.CompletedProcess(cmd, 0, "Path = setup.exe\nSize = 10\nAttributes = A\n\n", "")

    monkeypatch.setattr(archives.subprocess, "run", fake)
    assert archives.try_list_archive_members(image) == [("setup.exe", 10)]
    plain = tmp_path / "Plain.img"
    plain.write_bytes(b"\x00" * 100)
    assert archives.try_list_archive_members(plain) is None
