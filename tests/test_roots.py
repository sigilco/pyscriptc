# SPDX-License-Identifier: Apache-2.0
"""The module-root seam (issue #403): `roots.py` is pure data plus one lookup."""

from __future__ import annotations

from pathlib import Path

from pyscriptc.roots import ModuleRoot, RootSet, roots_for_directory


def _program_dir(tmp_path: Path) -> Path:
    (tmp_path / "__main__.py").write_text("print('hi')\n")
    (tmp_path / "util.py").write_text("X = 1\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "sub.py").write_text("")
    return tmp_path


def test_program_root_resolves_plain_module(tmp_path: Path) -> None:
    root_set = roots_for_directory(_program_dir(tmp_path))
    root = root_set.resolve("util")
    assert root is not None
    assert root.kind == "program"
    assert root.path == tmp_path.resolve()


def test_program_root_resolves_dotted_submodule(tmp_path: Path) -> None:
    root_set = roots_for_directory(_program_dir(tmp_path))
    for name in ("pkg", "pkg.sub", "pkg.sub.deeper"):
        root = root_set.resolve(name)
        assert root is not None, name
        assert root.kind == "program"


def test_program_root_unknown_name_is_none(tmp_path: Path) -> None:
    root_set = roots_for_directory(_program_dir(tmp_path))
    assert root_set.resolve("missing") is None
    assert root_set.resolve("") is None
    assert root_set.roots_for("missing") == []


def test_package_root_resolves_namespace(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    root_set = roots_for_directory(package)
    for name in ("pkg", "pkg.sub", "pkg.sub.deeper"):
        root = root_set.resolve(name)
        assert root is not None, name
        assert root.kind == "package"
        assert root.name == "pkg"
    assert root_set.resolve("other") is None
    assert root_set.resolve("pkg_extra") is None


def test_roots_for_lists_claimants(tmp_path: Path) -> None:
    program = tmp_path / "prog"
    program.mkdir()
    _program_dir(program)
    dep = tmp_path / "dep"
    dep.mkdir()
    (dep / "certifi").mkdir()
    (dep / "certifi" / "__init__.py").write_text("")
    root_set = RootSet(
        [
            *roots_for_directory(program).roots,
            ModuleRoot(name="dep", path=dep, kind="dependency"),
        ]
    )
    certifi = root_set.resolve("certifi")
    assert certifi is not None and certifi.path == dep
    util = root_set.resolve("util")
    assert util is not None and util.path == program.resolve()
    assert root_set.roots_for("certifi.core") == ["dep"]
