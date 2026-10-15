import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from endpoints import install
from handler.auth import get_current_user
from handler.install import proton_builds


@pytest.mark.parametrize("build_id", ["..", ".", "", "../x", "a/b", ".hidden", "x" * 101, "a b"])
def test_an_id_that_is_not_a_folder_name_is_refused(build_id):
    assert not proton_builds.is_valid_build_id(build_id)


@pytest.mark.parametrize("build_id", ["cachyos-latest", "GE-Proton10-4", "proton_9.0-1"])
def test_the_usual_ids_are_accepted(build_id):
    assert proton_builds.is_valid_build_id(build_id)


def test_removing_dot_dot_deletes_nothing(monkeypatch, tmp_path):
    root = tmp_path / "opt" / "proton"
    root.mkdir(parents=True)
    keep = tmp_path / "opt" / "keep.txt"
    keep.write_text("x")
    monkeypatch.setattr(proton_builds, "PROTON_INSTALL_ROOT", str(root))
    proton_builds.remove_build("..")
    assert keep.exists() and root.exists()


def test_the_routes_answer_404_for_a_dot_dot_id(monkeypatch):
    app = FastAPI()
    app.include_router(install.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_admin=True, hidden_library_ids=[])
    called = []
    monkeypatch.setattr(install, "remove_build", lambda build_id: called.append(build_id))
    client = TestClient(app)
    assert client.delete("/api/games/install/proton/%2E%2E").status_code in (404, 405)
    assert called == []


def test_the_digest_of_an_asset_is_read_from_github_listing():
    good = "a" * 64
    assert proton_builds._sha256_of({"digest": f"sha256:{good.upper()}"}) == good
    assert proton_builds._sha256_of({"digest": "sha1:abc"}) is None
    assert proton_builds._sha256_of({}) is None
