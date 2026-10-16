from handler.install.runner import _build_root


def test_a_wine_build_is_bound_with_its_libraries(tmp_path):
    """A plain Wine build under the builds folder (`<build>/bin/wine`, which the build list offers) was never bound into
    the sandbox, only Proton builds were: every install under it failed at once with "execvp .../bin/wine: No such file
    or directory". Its whole folder is bound, as its lib/ sits beside bin/."""
    wine = tmp_path / "wine-build" / "bin" / "wine"
    wine.parent.mkdir(parents=True)
    wine.write_text("#!/bin/sh\n")
    assert _build_root(str(wine)) == str(tmp_path / "wine-build")


def test_a_proton_build_is_bound_as_its_own_folder(tmp_path):
    proton = tmp_path / "GE-Proton10-1" / "proton"
    proton.parent.mkdir()
    proton.write_text("#!/usr/bin/env python3\n")
    assert _build_root(str(proton)) == str(tmp_path / "GE-Proton10-1")


def test_the_systems_wine_and_a_missing_build_need_no_bind(tmp_path):
    assert _build_root("wine") is None
    assert _build_root(str(tmp_path / "gone" / "proton")) is None
