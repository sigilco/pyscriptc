"""Issue #405: a re-export in an `__init__.py` binds like CPython.

`from .x import y` in a package `__init__.py` makes `y` a name of the
package, so `from pkg import y` binds. The compiled binary must print what
CPython prints on the same program: the differential shape every test here
follows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import compile_run, run_cli

ROOT = Path(__file__).resolve().parents[1]


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _oracle(package_root: Path, app: str, func: str, args: str) -> str:
    """CPython imports the same tree and prints the same call.

    The oracle runs the package's own `app` module with the tree's root on
    `sys.path`, so `from pkg import y` resolves exactly as the user's
    program does.
    """

    source = "\n".join(
        [
            "import importlib.util",
            f"spec = importlib.util.spec_from_file_location('reexp_app', r'{app}')",
            "mod = importlib.util.module_from_spec(spec)",
            "spec.loader.exec_module(mod)",
            f"print(mod.{func}(*{args}))",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=True,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
    )
    return proc.stdout


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_function_reexport_binds_from_the_package(tmp_path: Path) -> None:
    """`from certifi import where`: the spelling every user writes."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import where\n",
            "core.py": "def where() -> str:\n    return '/etc/ssl/cert.pem'\n",
            "app.py": "from pkg import where\n\ndef show() -> str:\n    return where()\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")
    assert got.stdout == "/etc/ssl/cert.pem\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_class_reexport_binds_from_the_package(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .shapes import Point\n",
            "shapes.py": "\n".join(
                [
                    "class Point:",
                    "    x: int",
                    "    def __init__(self, x: int) -> None:",
                    "        self.x = x",
                    "",
                ]
            ),
            "app.py": "from pkg import Point\n\ndef show() -> int:\n    return Point(4).x\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_constant_reexport_binds_from_the_package(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import VERSION\n",
            "core.py": "VERSION: str = 'v1'\n",
            "app.py": "from pkg import VERSION\n\ndef show() -> str:\n    return VERSION\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_module_reexport_binds_from_the_package(tmp_path: Path) -> None:
    """`from . import x` re-exports the submodule, attributes resolve."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from . import sub\n",
            "sub.py": "def deep() -> int:\n    return 7\n",
            "app.py": "from pkg import sub\n\ndef show() -> int:\n    return sub.deep()\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_an_alias_reexport_renames_the_binding(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import where as locate\n",
            "core.py": "def where() -> str:\n    return 'here'\n",
            "app.py": "from pkg import locate\n\ndef show() -> str:\n    return locate()\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_alias_chain_resolves_through_two_inits(tmp_path: Path) -> None:
    """The `__init__` re-exports from a submodule whose own `__init__` re-exports again."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .sub import deep, Point\n",
            "sub/__init__.py": "from .inner import deep, Point\n",
            "sub/inner.py": "\n".join(
                [
                    "def deep() -> int:",
                    "    return 7",
                    "",
                    "class Point:",
                    "    x: int",
                    "    def __init__(self, x: int) -> None:",
                    "        self.x = x",
                    "",
                ]
            ),
            "app.py": "\n".join(
                [
                    "from pkg import deep",
                    "from pkg import Point",
                    "",
                    "def show() -> int:",
                    "    return deep() + Point(1).x",
                    "",
                ]
            ),
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_defining_module_still_emits_the_function(tmp_path: Path) -> None:
    """The alias is a pointer: the submodule spelling binds the same function."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import where\n",
            "core.py": "def where() -> str:\n    return 'both'\n",
            "app.py": "\n".join(
                [
                    "from pkg import where",
                    "from pkg.core import where as core_where",
                    "",
                    "def show() -> str:",
                    "    return where() + core_where()",
                    "",
                ]
            ),
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


def test_a_reexport_of_a_missing_name_still_fences(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import missing\n",
            "core.py": "def where() -> str:\n    return 'x'\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "pkg.ir.json"))
    assert got.returncode == 3
    assert "pkg.core.missing is not exported by this package" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_package_attribute_reaches_the_reexport(tmp_path: Path) -> None:
    """`pkg.where` reaches the re-exported name, like CPython's module attribute."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "from .core import where\n",
            "core.py": "def where() -> str:\n    return 'attr'\n",
            "app.py": "import pkg\n\ndef show() -> str:\n    return pkg.where()\n",
        },
    )
    got = compile_run(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_program_imports_a_reexport_by_the_package_name(tmp_path: Path) -> None:
    """A program directory whose package dependency re-exports: `from pkg import y` binds."""

    root = _write_tree(
        tmp_path / "prog",
        {
            "pkg/__init__.py": "from .core import where\n",
            "pkg/core.py": "def where() -> str:\n    return 'main'\n",
            "__main__.py": "from pkg import where\n\nprint(where())\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--run", "-o", str(tmp_path / "prog-bin"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "main\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_a_program_imports_a_reexported_class(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "prog",
        {
            "pkg/__init__.py": "from .shapes import Point\n",
            "pkg/shapes.py": "\n".join(
                [
                    "class Point:",
                    "    x: int",
                    "    def __init__(self, x: int) -> None:",
                    "        self.x = x",
                    "",
                ]
            ),
            "__main__.py": "from pkg import Point\n\nprint(Point(3).x)\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--run", "-o", str(tmp_path / "prog-class"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "3\n"


def test_vendored_certifi_reexport_compiles_clean(tmp_path: Path) -> None:
    """The pilot tree: vendored certifi compiles with no export-cascade fences."""

    src = ROOT / "benchmarks" / "compat" / "top100" / "src"
    certifi = src / "certifi"
    report = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "coverage", str(certifi), "--json"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(report.stdout)["summary"]
    assert summary["rootFences"] == 0, summary
    assert summary["byFamily"].get("export-cascade", 0) == 0, summary
