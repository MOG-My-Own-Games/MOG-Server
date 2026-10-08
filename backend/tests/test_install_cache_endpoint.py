import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from endpoints import install
from handler import mods


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mods, "MODS_CACHE_PATH", str(tmp_path / "cache"))
    monkeypatch.setattr(mods, "_jobs", {})
    monkeypatch.setattr(install, "cache_root_dirs", lambda: [])
    folder = tmp_path / "cache" / "3"
    folder.mkdir(parents=True)
    (folder / "mod1.zip").write_bytes(b"zz")
    return folder


def test_the_cache_listing_shows_zipped_mods_apart_from_the_installers(cache):
    result = asyncio.run(install.list_install_cache(SimpleNamespace()))

    assert result.entries == []
    assert [(m.game_id, m.file_name, m.size_bytes) for m in result.mods] == [(3, "mod1.zip", 2)]


def test_mods_routes_come_before_the_session_route_so_mods_is_not_read_as_a_session_id():
    paths = [r.path for r in install.router.routes if "DELETE" in getattr(r, "methods", ())]
    assert paths.index("/games/install/cache/mods") < paths.index("/games/install/cache/{session_id}")


def test_one_zipped_mod_can_be_deleted_and_a_missing_one_is_a_404(cache):
    asyncio.run(install.clear_one_mod_cache(SimpleNamespace(), 3, "mod1.zip"))
    assert not (cache / "mod1.zip").exists()

    with pytest.raises(HTTPException) as err:
        asyncio.run(install.clear_one_mod_cache(SimpleNamespace(), 3, "mod1.zip"))
    assert err.value.status_code == 404


def test_all_zipped_mods_can_be_cleared(cache):
    (cache / "mod2.zip").write_bytes(b"y")
    assert asyncio.run(install.clear_mod_cache(SimpleNamespace())).cleared == 2
