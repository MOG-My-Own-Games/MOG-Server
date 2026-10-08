import json
from types import SimpleNamespace

import httpx
import pytest

import config
from endpoints import games as games_endpoint
from handler.metadata import ludusavi_handler as ludusavi

MANIFEST = """---
"Stardew Valley":
  files:
    "<home>/.config/StardewValley/Saves":
      tags:
        - save
      when:
        - os: mac
    "<winAppData>/StardewValley/Saves":
      tags:
        - save
      when:
        - os: windows
    "<xdgConfig>/StardewValley/Saves":
      tags:
        - save
      when:
        - os: linux
    "<xdgConfig>/StardewValley":
      tags:
        - config
      when:
        - os: linux
  steam:
    id: 413150
"0 A.D.":
  files:
    "<xdgData>/0ad/saves":
      tags:
        - save
    "<storeUserId>/slots":
      tags:
        - save
"Windows Only":
  files:
    "<winDocuments>/Game":
      tags:
        - save
"Install Dir Saves":
  files:
    "<base>/saves":
      tags:
        - save
      when:
        - os: linux
          store: steam
"No Files":
  steam:
    id: 1
"Weird: Name":
  files:
    "<home>/.weird":
      tags:
        - save
      when:
        - os: linux
"""


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LUDUSAVI_CACHE_PATH", str(tmp_path / "ludusavi"))
    monkeypatch.setattr(ludusavi, "_loaded", None)
    return tmp_path / "ludusavi"


def test_only_linux_save_paths_a_client_can_resolve_are_kept(tmp_path):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(MANIFEST)

    index = ludusavi.build_index(manifest)

    assert index == {
        "stardewvalley": ["<xdgConfig>/StardewValley/Saves"],  # not the mac, windows or config entries
        "0ad": ["<xdgData>/0ad/saves"],  # no condition means any OS; the store user id cannot be resolved
        "installdirsaves": ["<base>/saves"],
        "weirdname": ["<home>/.weird"],
    }


def test_a_game_is_found_by_any_of_its_names_ignoring_case_and_punctuation(cache):
    cache.mkdir()
    (cache / "index.json").write_text(json.dumps({"stardewvalley": ["<xdgConfig>/StardewValley/Saves"]}))

    assert ludusavi.paths_for("Nope", "stardew VALLEY!") == ["<xdgConfig>/StardewValley/Saves"]
    assert ludusavi.paths_for(None, "Nope") == [] and ludusavi.paths_for() == []


def test_with_no_manifest_yet_there_is_just_no_answer(cache):
    assert ludusavi.paths_for("Stardew Valley") == []


class Response:
    def __init__(self, status=200, body=b"", etag=None):
        self.status_code, self.body, self.headers = status, body, ({"etag": etag} if etag else {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=self)

    def iter_bytes(self, size):
        yield self.body


def test_a_download_replaces_the_copy_and_a_later_unchanged_one_does_nothing(cache, monkeypatch):
    sent = []

    def stream(method, url, headers, **kwargs):
        sent.append(headers)
        return Response(304) if headers.get("If-None-Match") == '"v1"' else Response(body=MANIFEST.encode(), etag='"v1"')

    monkeypatch.setattr(httpx, "stream", stream)

    assert ludusavi.refresh() is True
    assert ludusavi.paths_for("Stardew Valley") == ["<xdgConfig>/StardewValley/Saves"]
    assert ludusavi.refresh() is False
    assert sent == [{}, {"If-None-Match": '"v1"'}]


def test_a_failed_download_keeps_answering_from_the_copy_kept(cache, monkeypatch):
    cache.mkdir()
    (cache / "index.json").write_text(json.dumps({"stardewvalley": ["<xdgConfig>/StardewValley/Saves"]}))

    def stream(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "stream", stream)

    assert ludusavi.refresh() is False
    assert ludusavi.paths_for("Stardew Valley") == ["<xdgConfig>/StardewValley/Saves"]
    assert not list(cache.glob("*.part"))


def test_switched_off_it_never_starts(monkeypatch):
    monkeypatch.setattr(config, "LUDUSAVI_ENABLED", False)
    assert ludusavi.start() is None


def test_the_endpoint_answers_by_the_games_own_name_or_igdbs(cache, monkeypatch):
    import asyncio

    cache.mkdir()
    (cache / "index.json").write_text(json.dumps({"stardewvalley": ["<xdgConfig>/StardewValley/Saves"]}))
    game = SimpleNamespace(name="stardew_valley_gog", igdb_metadata={"name": "Stardew Valley"})
    monkeypatch.setattr(games_endpoint.db_game_handler, "get_game", lambda id: game if id == 1 else None)

    found = asyncio.run(games_endpoint.get_game_save_paths(SimpleNamespace(), 1))
    assert found.paths == ["<xdgConfig>/StardewValley/Saves"]

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as err:
        asyncio.run(games_endpoint.get_game_save_paths(SimpleNamespace(), 2))
    assert err.value.status_code == 404
