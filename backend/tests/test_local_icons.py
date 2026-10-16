import io
import struct
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from endpoints import games as games_endpoints
from handler import local_icons, media_uploads
from handler import media as media_handler
from handler.auth import get_current_user
from handler.database import db_game_handler, db_library_handler, db_user_handler
from models.game import Game
from models.library import Library
from models.user import Role, User
from utils import image_cache


def _png(size=(48, 48), color=(20, 160, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color + (255,)).save(buf, "PNG")
    return buf.getvalue()


def _ico(*sizes, color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", max(sizes), color + (255,)).save(buf, "ICO", sizes=list(sizes))
    return buf.getvalue()


def _directory(entries: list[tuple[int, int, bool]]) -> bytes:
    """A resource directory: (id, offset, is a directory) for each entry."""
    out = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries))
    for ident, offset, is_dir in entries:
        out += struct.pack("<II", ident, offset | (0x80000000 if is_dir else 0))
    return out


def make_exe(icon_png: bytes | None, width: int = 48) -> bytes:
    """A small PE file whose only resource is one icon group with one PNG picture (what Windows keeps big icons as)."""
    resources = b""
    if icon_png is not None:
        # Offsets in the table: the root, then a type, a name and a language directory for each of the two types, then the data
        # entries and the data. RT_ICON (3) comes before RT_GROUP_ICON (14), as the ids are sorted.
        t3, t14, n3, n14, l3, l14, d3, d14, data = 32, 56, 80, 104, 128, 152, 176, 192, 208
        group = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHIH", width % 256, width % 256, 0, 0, 1, 32, len(icon_png), 1)
        rva = 0x2000
        resources = _directory([(3, t3, True), (14, t14, True)])
        for here, below in ((t3, n3), (t14, n14), (n3, l3), (n14, l14)):
            assert len(resources) == here
            resources += _directory([(1, below, True)])
        for here, entry in ((l3, d3), (l14, d14)):
            assert len(resources) == here
            resources += _directory([(1033, entry, False)])
        resources += struct.pack("<IIII", rva + data + len(group), len(icon_png), 0, 0)  # d3: the picture
        resources += struct.pack("<IIII", rva + data, len(group), 0, 0)  # d14: the group
        assert len(resources) == data
        resources += group + icon_png
    headers = bytearray(0x400)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 60, 0x80)
    headers[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", headers, 0x84, 0x14C, 1, 0, 0, 0, 224, 0x102)  # machine, sections, ..., optional size, flags
    optional = 0x98
    struct.pack_into("<H", headers, optional, 0x10B)
    struct.pack_into("<II", headers, optional + 96 + 16, 0x2000 if resources else 0, len(resources))
    section = optional + 224
    headers[section : section + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", headers, section + 8, len(resources), 0x2000, len(resources), 0x400)
    return bytes(headers) + resources


@pytest.fixture
def world(db, monkeypatch, tmp_path):
    monkeypatch.setattr(media_uploads, "RESOURCES_BASE_PATH", str(tmp_path / "resources"))
    monkeypatch.setattr(image_cache, "CACHE_DIR", tmp_path / "resources" / "imagecache")
    monkeypatch.setattr(media_handler.sgdb_handler, "get_media", lambda *_: {"icon": [{"url": "https://cdn2.steamgriddb.com/i.png"}]})
    app = FastAPI()
    app.include_router(games_endpoints.router, prefix="/api")
    admin = db_user_handler.add_user(User(username="admin", hashed_password="x", role=Role.ADMIN))
    app.dependency_overrides[get_current_user] = lambda: admin
    lib = db_library_handler.add_library(Library(name="L", root_path=str(tmp_path / "lib")))
    folder = tmp_path / "lib" / "Zero Zone"
    folder.mkdir(parents=True)
    game = db_game_handler.add_game(Game(library_id=lib.id, fs_name="Zero Zone", name="Zero Zone", sgdb_id=5))
    return SimpleNamespace(client=TestClient(app), game=game, folder=folder, root=tmp_path / "resources" / "media")


def test_the_icon_inside_an_exe_is_read_and_drawn_as_a_png(tmp_path):
    exe = tmp_path / "game.exe"
    exe.write_bytes(make_exe(_png((64, 64)), width=64))

    picture = local_icons.to_png(local_icons.exe_icon(exe))

    assert picture is not None
    data, width, height = picture
    assert (width, height) == (64, 64) and Image.open(io.BytesIO(data)).format == "PNG"


@pytest.mark.parametrize("content", [b"", b"MZ" + b"\0" * 10, b"not an executable at all", make_exe(None), make_exe(_png())[:600]])
def test_a_file_that_is_not_a_readable_exe_with_an_icon_gives_none(tmp_path, content):
    exe = tmp_path / "x.exe"
    exe.write_bytes(content)
    assert local_icons.exe_icon(exe) is None


def test_the_largest_picture_of_an_ico_is_kept_and_a_tiny_icon_is_not(tmp_path):
    assert local_icons.to_png(_ico((16, 16), (32, 32), (128, 128)))[1:] == (128, 128)
    assert local_icons.to_png(_ico((8, 8))) is None  # too small to be an icon worth offering
    assert local_icons.to_png(b"garbage") is None


def test_uninstallers_crash_reporters_and_prerequisites_are_not_looked_at(tmp_path):
    for name in ("Game.exe", "unins000.exe", "UnityCrashHandler64.exe", "vcredist_x86.exe", "dxwebsetup.exe", "icon.ico"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "_CommonRedist").mkdir()
    (tmp_path / "_CommonRedist" / "setup.exe").write_bytes(b"x")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "launcher.exe").write_bytes(b"x")
    (tmp_path / "bin" / "python.exe").write_bytes(b"x")  # what a Ren'Py game ships to run itself
    (tmp_path / "pfx" / "drive_c" / "windows").mkdir(parents=True)  # a Wine prefix kept next to the game
    (tmp_path / "pfx" / "drive_c" / "windows" / "notepad.exe").write_bytes(b"x")

    assert [p.relative_to(tmp_path).as_posix() for p in local_icons.files_of(tmp_path)] == ["Game.exe", "icon.ico", "bin/launcher.exe"]


def test_the_scrape_offers_the_icons_of_the_games_files_after_the_providers_and_never_picks_one_itself(world):
    (world.folder / "Zero Zone.exe").write_bytes(make_exe(_png((64, 64)), width=64))
    (world.folder / "app.ico").write_bytes(_ico((32, 32), (64, 64), color=(10, 10, 200)))
    (world.folder / "again.ico").write_bytes((world.folder / "app.ico").read_bytes())  # the same picture counts once

    data = world.client.get(f"/api/games/{world.game.id}/media/candidates").json()

    icons = data["candidates"]["icon"]
    assert [i["source"] for i in icons] == ["steamgriddb", "local", "local"]
    assert [i["label"] for i in icons[1:]] == ["again.ico", "Zero Zone.exe"]  # by name, the same picture once
    assert all(i["url"].startswith(f"/resources/media/{world.game.id}/found/") for i in icons[1:])
    assert data["defaults"]["icon"]["source"] == "steamgriddb"  # the providers' own pick, not a file's


def test_without_a_provider_icon_a_file_icon_is_on_offer_but_not_the_default(world, monkeypatch):
    monkeypatch.setattr(media_handler.sgdb_handler, "get_media", lambda *_: {})
    (world.folder / "app.ico").write_bytes(_ico((64, 64)))

    data = world.client.get(f"/api/games/{world.game.id}/media/candidates").json()

    assert len(data["candidates"]["icon"]) == 1
    assert "icon" not in data["defaults"]


def test_a_folder_is_not_read_again_while_its_files_are_as_they_were(world, monkeypatch):
    (world.folder / "app.ico").write_bytes(_ico((64, 64)))
    world.client.get(f"/api/games/{world.game.id}/media/candidates")
    calls = []
    monkeypatch.setattr(local_icons, "_picture", lambda path: calls.append(path))

    world.client.get(f"/api/games/{world.game.id}/media/candidates")
    assert calls == []
    (world.folder / "app.ico").write_bytes(_ico((96, 96)))  # changed: read again
    world.client.get(f"/api/games/{world.game.id}/media/candidates")
    assert len(calls) == 1


def test_choosing_an_icon_of_the_games_files_makes_it_the_games_own_upload(world):
    (world.folder / "app.ico").write_bytes(_ico((64, 64)))
    url = world.client.get(f"/api/games/{world.game.id}/media/candidates").json()["candidates"]["icon"][-1]["url"]

    response = world.client.put(f"/api/games/{world.game.id}/media", json={"icon": url})

    assert response.status_code == 200
    chosen = response.json()["media"]["icon"]
    assert chosen["source"] == "upload" and chosen["url"].startswith(f"/resources/media/{world.game.id}/icon-")
    assert (world.root / str(world.game.id) / chosen["url"].rsplit("/", 1)[1]).is_file()


def test_an_icon_found_for_another_game_cannot_be_chosen(world):
    (world.folder / "app.ico").write_bytes(_ico((64, 64)))
    url = world.client.get(f"/api/games/{world.game.id}/media/candidates").json()["candidates"]["icon"][-1]["url"]
    other = db_game_handler.add_game(Game(library_id=world.game.library_id, fs_name="o", name="O"))

    response = world.client.put(f"/api/games/{other.id}/media", json={"icon": url})

    assert response.status_code == 400
    assert local_icons.read(url, other.id) is None


def test_the_local_icons_come_alone_from_their_own_endpoint_without_asking_the_providers(world, monkeypatch):
    monkeypatch.setattr(media_handler.sgdb_handler, "get_media", lambda *_: pytest.fail("the providers were asked"))
    (world.folder / "app.ico").write_bytes(_ico((64, 64)))

    response = world.client.get(f"/api/games/{world.game.id}/media/local-icons")

    assert response.status_code == 200
    assert [(c["source"], c["label"]) for c in response.json()["candidates"]] == [("local", "app.ico")]


def test_a_game_that_is_a_single_file_has_no_local_icons(world):
    (world.folder.parent / "single.zip").write_bytes(b"PK")
    single = db_game_handler.add_game(Game(library_id=world.game.library_id, fs_name="single.zip", name="S"))

    assert world.client.get(f"/api/games/{single.id}/media/local-icons").json() == {"candidates": []}
