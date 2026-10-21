"""A program directory compiles as one binary: `__main__.py` is the entry.

The oracle is CPython itself: the same tree run with `python3 __main__.py`
must produce the same stdout and exit code as the compiled binary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli

ROOT = Path(__file__).resolve().parents[1]


def write_tree(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def oracle_run(root: Path, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "__main__.py", *(args or [])],
        cwd=root,
        capture_output=True,
        text=True,
    )


def compile_run_program(tmp_path: Path, root: Path, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    return run_cli(
        "compile",
        str(root),
        "--skip-check",
        "--run",
        "-o",
        str(tmp_path / "prog"),
        *(["--", *(args or [])] if args else []),
    )


def assert_matches_cpython(tmp_path: Path, root: Path, args: list[str] | None = None) -> None:
    expected = oracle_run(root, args)
    got = compile_run_program(tmp_path, root, args)
    assert got.returncode == 0, got.stderr
    assert got.stdout == expected.stdout, f"stderr: {got.stderr}\nexpected: {expected.stdout}"
    assert got.returncode == expected.returncode


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_program_entry_and_plain_imports_match_cpython(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "words",
        {
            "__main__.py": "import util\nfrom pkg import x\n\nprint(util.twice(21))\nprint(x.NAME)\n",
            "util.py": "def twice(n: int) -> int:\n    return n * 2\n",
            "pkg/__init__.py": "from . import x\n",
            "pkg/x.py": "NAME: str = 'from-x'\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_module_bodies_run_in_import_order(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "order",
        {
            "__main__.py": "import first\nimport second\n\nprint('main')\n",
            "first.py": "print('first')\n\n\ndef one() -> int:\n    return 1\n",
            "second.py": "import late\n\nprint('second')\n",
            "late.py": "print('late')\n\n\ndef three() -> int:\n    return 3\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_module_body_runs_once_when_imported_twice(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "once",
        {
            "__main__.py": "import a\nimport b\n\nprint(a.one() + b.two())\n",
            "a.py": "print('init a')\n\n\ndef one() -> int:\n    return 1\n",
            "b.py": "import a\n\nprint('init b')\n\n\ndef two() -> int:\n    return 2\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_from_import_binds_module_members(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "members",
        {
            "__main__.py": (
                "from util import twice\nfrom pkg.helper import NAME\n"
                "from shapes import Point\n\n"
                "print(twice(4))\nprint(NAME)\nprint(Point(3).x)\n"
            ),
            "util.py": "def twice(n: int) -> int:\n    return n * 2\n",
            "pkg/__init__.py": "",
            "pkg/helper.py": "NAME: str = 'helper'\n",
            "shapes.py": ("class Point:\n    x: int\n\n    def __init__(self, x: int) -> None:\n        self.x = x\n"),
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_program_with_a_main_guard_still_runs_the_top_level(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "guarded",
        {
            "__main__.py": ("import util\n\nprint('top')\n\nif __name__ == \"__main__\":\n    print(util.twice(5))\n"),
            "util.py": "def twice(n: int) -> int:\n    return n * 2\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_sys_argv_reaches_the_program(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "argvp",
        {
            "__main__.py": "import sys\n\nprint(sys.argv[1])\nprint(len(sys.argv))\n",
        },
    )
    assert_matches_cpython(tmp_path, root, ["hello", "world"])


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_main_guard_in_an_imported_module_does_not_run(tmp_path: Path) -> None:
    # An imported module is not `__main__`: its guard body never runs.
    root = write_tree(
        tmp_path,
        "guardedmod",
        {
            "__main__.py": "import helper\n\nprint(helper.VALUE)\nprint(helper.make())\n",
            "helper.py": (
                "VALUE: str = 'constant'\n\n"
                'if __name__ == "__main__":\n'
                "    print('should not run')\n\n\n"
                "def make() -> str:\n"
                "    return VALUE + '!'\n"
            ),
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_side_effect_import_runs_the_body_once(tmp_path: Path) -> None:
    # The same module imported twice runs its body once.
    root = write_tree(
        tmp_path,
        "sideeffect",
        {
            "__main__.py": "import side\nfrom side import MARK\n\nprint(MARK)\n",
            "side.py": "print('side effect runs')\n\nMARK: str = 'mark'\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_module_nobody_imports_never_runs(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "unreachable",
        {
            "__main__.py": "import shared\n\nprint('main')\n",
            "aa.py": "import shared\n\nprint('init aa')\n",
            "shared.py": "print('init shared')\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_a_nested_package_resolves_by_its_plain_name(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "nested",
        {
            "__main__.py": "import pkg.sub.deep\nfrom pkg import mid\n\nprint(mid.MID)\nprint(pkg.sub.deep.DEEP)\n",
            "pkg/__init__.py": "MID: str = 'mid'\n",
            "pkg/mid.py": "MID: str = 'mid-module'\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/deep.py": "DEEP: str = 'deep'\n",
        },
    )
    assert_matches_cpython(tmp_path, root)


def test_a_circular_import_is_refused_with_the_cycle_path(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "cycle",
        {
            "__main__.py": "import a\n\nprint(a.one())\n",
            "a.py": "import b\n\nprint('init a')\n\n\ndef one() -> int:\n    return 1\n",
            "b.py": "import a\n\nprint('init b')\n\n\ndef two() -> int:\n    return 2\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "cycle.ir.json"))
    assert got.returncode == 3, got.stderr
    assert "circular import: a -> b -> a" in got.stderr


def test_a_self_import_is_refused(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "selfimp",
        {
            "__main__.py": "import selfmod\n\nprint(selfmod.f())\n",
            "selfmod.py": "import selfmod\n\n\ndef f() -> int:\n    return 1\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "self.ir.json"))
    assert got.returncode == 3, got.stderr
    assert "circular import: selfmod -> selfmod" in got.stderr


def test_a_cycle_inside_a_package_is_refused(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "pkgcycle",
        {
            "__main__.py": "import pkg.a\n\nprint(pkg.a.f())\n",
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n\n\ndef f() -> int:\n    return b.g()\n",
            "pkg/b.py": "from pkg import a\n\n\ndef g() -> int:\n    return a.f()\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "pc.ir.json"))
    assert got.returncode == 3, got.stderr
    assert "circular import: pkg.a -> pkg.b -> pkg.a" in got.stderr


def test_a_package_directory_still_skips_dunder_main(tmp_path: Path) -> None:
    # A root __init__.py marks a library package: __main__.py stays the
    # python -m script and is skipped, not compiled as the entry.
    root = write_tree(
        tmp_path,
        "lib",
        {
            "__init__.py": "",
            "util.py": "def twice(n: int) -> int:\n    return n * 2\n",
            "__main__.py": "import argparse\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "lib.ir.json"))
    assert "skipped lib/__main__.py" in got.stderr


def test_a_missing_program_module_fences_clearly(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "missing",
        {
            "__main__.py": "import nosuch\n\nprint(nosuch.f())\n",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "m.ir.json"))
    assert got.returncode == 3, got.stderr
    assert "nosuch is not in the closed import table" in got.stderr


def test_a_relative_import_in_a_plain_sibling_is_refused(tmp_path: Path) -> None:
    root = write_tree(
        tmp_path,
        "relplain",
        {
            "__main__.py": "import util\n\nprint(util.f())\n",
            "util.py": "from . import other\n\n\ndef f() -> int:\n    return 1\n",
            "other.py": "",
        },
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "r.ir.json"))
    assert got.returncode == 3, got.stderr
    assert "no parent package" in got.stderr


def test_the_program_entry_is_the_standalone_run(tmp_path: Path) -> None:
    # The merged IR names the entry __main__.main; --run without --call
    # executes it.
    root = write_tree(
        tmp_path,
        "entryname",
        {
            "__main__.py": "import util\n\nprint(util.twice(2))\n",
            "util.py": "def twice(n: int) -> int:\n    return n * 2\n",
        },
    )
    got = compile_run_program(tmp_path, root)
    assert got.returncode == 0, got.stderr
    assert got.stdout == "4\n"
