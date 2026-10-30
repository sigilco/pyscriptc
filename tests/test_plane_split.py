"""The per-module plane split (the island's granularity is the module).

Under `--fallback=cpython` a program whose units are plain siblings keeps
both lanes: a fenced module rides its own island floor and the clean
modules lower native, with a cross-module call crossing the boundary
through the boxed seam (`ps_island_mod_fn_invoke`, one island namespace
per program module). Every case diffs the compiled binary against CPython.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _compile(tmp_path: Path, prog: Path, *flags: str) -> tuple[Path, subprocess.CompletedProcess[str]]:
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), *flags, cwd=tmp_path
    )
    return binary, comp


def _compare(tmp_path: Path, prog: Path) -> str:
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    return got.stdout


def _plane_map(tmp_path: Path, prog: Path) -> dict:
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    report = json.loads((binary.parent / f"{binary.name}.report.json").read_text(encoding="utf-8"))
    return report["planeMap"]


def test_three_module_program_splits_the_planes(tmp_path: Path) -> None:
    """`clean.add` and `__main__` stay native; the fenced module rides the island.

    The three-module shape of the plane split: `clean.add` is fully
    annotated, `dirty.broken` is not, and the entry calls both. The
    program keeps the native lane for the two clean definitions and
    crosses the boundary for the fenced one.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "3\nX\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["clean.add"] == "native"
    assert planes["__main__.main"] == "native"
    assert planes["dirty.broken"] == "island"


def test_split_keeps_the_native_definitions_in_the_binary(tmp_path: Path) -> None:
    """The native lane is real: the binary carries `clean.add` as LLVM code."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    assert "define i64 @clean.add" in llvm
    assert "define void @__main__.main" in llvm
    # The boundary call is the boxed seam, namespaced by the defining module.
    assert "ps_island_mod_fn_invoke_value" in llvm
    assert "ps_island_mod_seed" in llvm


def test_two_fenced_modules_keep_their_own_namespaces(tmp_path: Path) -> None:
    """Two fenced modules with the same definition name stay apart.

    The island namespace is per module, so `one.work` and `two.work` are
    two different functions; the namespaced seam keeps them apart.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from one import work as work_one\n"
                "from two import work as work_two\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    first: str = work_one('a')\n"
                "    second: str = work_two('b')\n"
                "    print(first, second)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "one.py": "def work(s):\n    return s + '1'\n",
            "two.py": "def work(s):\n    return s + '2'\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "a1 b2\n"


def test_fenced_module_with_a_native_sibling(tmp_path: Path) -> None:
    """One module, one fenced definition and one clean one: the clean one stays native."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from mixed import clean_one, dirty_one\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    value: int = clean_one(3)\n"
                "    print(value)\n"
                "    text: str = dirty_one('y')\n"
                "    print(text)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "mixed.py": (
                "def clean_one(n: int) -> int:\n    return n + 1\n"
                "\n"
                "\n"
                "def dirty_one(s):\n    return s.upper()\n"
            ),
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "4\nY\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["mixed.clean_one"] == "native"
    assert planes["mixed.dirty_one"] == "island"


def test_island_definition_calling_a_native_sibling_refuses(tmp_path: Path) -> None:
    """The island-to-compiled seam refuses across modules too.

    An island definition calling a native definition of another module
    has no seam to cross: the native definition exists only in the
    compiled lane. The refusal names the call, so the fix is visible.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    print(broken('x'))\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "from clean import add\n\n\ndef broken(s):\n    return s.upper() + str(add(1, 1))\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 3, comp.stdout
    assert "PS2002" in comp.stderr
    assert "clean.add" in comp.stderr
    assert "island-to-compiled seam" in comp.stderr


def test_package_shaped_program_still_runs_whole_island(tmp_path: Path) -> None:
    """A package program keeps the whole-program island.

    The per-module seed cannot reproduce the package semantics (the
    relative imports and the package `__init__` body), so a package
    program runs whole island-side, as before.
    """
    pkg = tmp_path / "pkg"
    _write(
        pkg,
        {
            "__init__.py": "print('init', __name__)\n",
            "__main__.py": "from pkg.cli import main\nmain()\n",
            "cli.py": (
                "from . import util\n"
                "from .util import shout\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(shout('hi'), util.where())\n"
            ),
            "util.py": (
                "import os\n"
                "\n"
                "\n"
                "def shout(s):\n"
                "    return s.upper()\n"
                "\n"
                "\n"
                "def where() -> str:\n"
                "    return os.path.basename(__file__)\n"
            ),
        },
    )
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(pkg), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run(
        [sys.executable, "-m", "pkg"], capture_output=True, text=True, timeout=60, cwd=str(pkg.parent)
    )
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout


def test_split_program_runs_away_from_the_source_tree(tmp_path: Path) -> None:
    """The split binary does not need the program directory at run time.

    The island namespace carries the fenced module's own definitions, so
    the binary does not import the program's modules from disk.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=elsewhere)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)


def test_island_definition_raising_propagates(tmp_path: Path) -> None:
    """An exception out of the island definition reaches the native caller."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from dirty import boom\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    try:\n"
                "        boom('boom')\n"
                "    except ValueError:\n"
                "        print('caught')\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "dirty.py": "def boom(s):\n    raise ValueError(s)\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout) == (want.returncode, want.stdout)
    assert got.stdout == "caught\n"
