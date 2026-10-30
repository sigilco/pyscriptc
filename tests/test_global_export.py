"""The export table carries the global slots a module binds.

A module-level global that lives in a runtime slot (a list, a dict, a
threaded record) keeps one pointer for the process. When another module
reads it, the read has to resolve natively: the export table carries the
slot, and the consumer reads the defining module's qualified key in the
runtime registry.

The differential shape every test here follows: the compiled binary prints
what CPython prints on the same tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli


def compile_program(tmp_path: Path, root: Path, func: str) -> subprocess.CompletedProcess[str]:
    """Compile the tree as a program and call `func` in the binary."""

    return run_cli(
        "compile",
        str(root),
        "--program",
        "--run",
        "--call",
        func,
        "-o",
        str(tmp_path / func),
    )


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _oracle(package_root: Path, app: str, func: str, args: str) -> str:
    """CPython imports the same tree and prints the same call."""

    source = "\n".join(
        [
            "import importlib.util",
            f"spec = importlib.util.spec_from_file_location('gexp_app', r'{app}')",
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


def test_a_list_global_reads_across_modules(tmp_path: Path) -> None:
    """`from mod import NAMES`: the list is one object, read natively."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "",
            "mod.py": "NAMES: list[str] = ['a', 'b']\n",
            "app.py": (
                "from pkg.mod import NAMES\n\n"
                "def show() -> int:\n"
                "    NAMES[0] = 'z'\n"
                "    return len(NAMES) + len(NAMES[0])\n"
            ),
        },
    )
    got = compile_program(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")
    assert got.stdout == "3\n"


def test_a_dict_global_reads_across_modules(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "",
            "mod.py": "TABLE: dict[str, int] = {'x': 1}\n",
            "app.py": (
                "from pkg.mod import TABLE\n\n"
                "def show() -> int:\n"
                "    TABLE['y'] = 2\n"
                "    return TABLE['x'] + TABLE['y']\n"
            ),
        },
    )
    got = compile_program(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")
    assert got.stdout == "3\n"


def test_a_write_in_one_module_is_seen_in_the_other(tmp_path: Path) -> None:
    """The slot is one object: the write lands in the defining module."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "",
            "mod.py": ("COUNTS: list[int] = [0]\n\ndef bump() -> None:\n    COUNTS[0] = COUNTS[0] + 1\n"),
            "app.py": (
                "from pkg.mod import COUNTS, bump\n\ndef show() -> int:\n    bump()\n    bump()\n    return COUNTS[0]\n"
            ),
        },
    )
    got = compile_program(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")
    assert got.stdout == "2\n"


def test_a_star_import_brings_the_global_slot(tmp_path: Path) -> None:
    """`from mod import *`: the slot comes with the other names."""

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "",
            "mod.py": "NAMES: list[str] = ['a', 'b']\n",
            "app.py": ("from pkg.mod import *\n\ndef show() -> int:\n    return len(NAMES)\n"),
        },
    )
    got = compile_program(tmp_path, root, "pkg.app.show")
    assert got.returncode == 0, got.stderr
    assert got.stdout == _oracle(tmp_path, root / "app.py", "show", "[]")
    assert got.stdout == "2\n"


def test_a_global_slot_export_carries_its_type(tmp_path: Path) -> None:
    """The export table entry names the defining module and the slot type."""

    from pyscriptc.package import _catalog_once

    root = _write_tree(
        tmp_path / "pkg",
        {
            "__init__.py": "",
            "mod.py": "NAMES: list[str] = ['a']\nCOUNT: int = 3\n",
        },
    )
    exports, _failed, _memo = _catalog_once(
        [
            ("pkg", root / "__init__.py", True),
            ("pkg.mod", root / "mod.py", False),
        ],
        {},
        program=False,
    )
    item = exports["pkg.mod"]["NAMES"]
    assert item["kind"] == "global"
    assert item["module"] == "pkg.mod"
    assert item["name"] == "NAMES"
    assert item["type"]["kind"] == "list"
    # A folded constant is not a slot: it exports as a constant, the way
    # CPython reads it (a fresh value at every read is the same value).
    assert exports["pkg.mod"]["COUNT"]["kind"] == "const"
