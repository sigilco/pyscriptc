"""Program-wide island for multi-module programs (Refs #641).

Under `--fallback=cpython` a program directory with any unit on the island
runs whole island-side through an in-memory finder, so imports, relative
imports, cycles, `__name__`, `__file__` and import order are CPython's own.
Every case diffs the compiled binary against CPython.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=180
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _compare(tmp_path: Path, target: Path, cpython_cmd: list[str], *flags: str) -> str:
    binary = tmp_path / "bin"
    comp = _cli("compile", str(target), "--fallback=cpython", "--emit", "bin", "-o", str(binary), *flags, cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary), "x", "y"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([*cpython_cmd, "x", "y"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    return got.stdout


def test_program_dir_sibling_with_fenced_function(tmp_path: Path) -> None:
    """The #641 shape: an imported module holds a function the island must own."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "from helper import count\nimport sys\nprint(count('a b c'), __name__, len(sys.argv))\n",
            "helper.py": "import shlex\n\n\ndef count(s: str) -> int:\n    return len(shlex.split(s))\n",
        },
    )
    out = _compare(tmp_path, prog, [sys.executable, str(prog)])
    assert out == "3 __main__ 3\n"


def test_package_relative_imports_cycle_and_order(tmp_path: Path) -> None:
    """`python -m pkg` shape: relative imports, a cycle, module `__name__`, import order."""
    pkg = tmp_path / "src" / "mp"
    _write(
        pkg,
        {
            "__init__.py": "print('init', __name__)\n",
            "__main__.py": "from mp.cli import main\nmain()\n",
            "cli.py": (
                "print('cli', __name__)\n"
                "from . import util\n"
                "from .util import shout\n\n\n"
                "def main():\n"
                "    print(shout('hi'), util.where())\n"
            ),
            "util.py": (
                "print('util', __name__)\n"
                "import os\n"
                "from . import cli\n\n\n"
                "def shout(s):\n"
                "    return s.upper() + str(len(cli.__name__))\n\n\n"
                "def where():\n"
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
        [sys.executable, "-m", "mp"], capture_output=True, text=True, timeout=60, cwd=tmp_path / "src"
    )
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout == "init mp\ncli mp.cli\nutil mp.util\nHI6 util.py\n"


def test_clean_program_stays_native(tmp_path: Path) -> None:
    """A fully typed program is not promoted: no island seed in the IR."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "from helper import twice\nprint(twice(4))\n",
            "helper.py": "def twice(x: int) -> int:\n    return x * 2\n",
        },
    )
    comp = _cli("compile", str(prog), "--fallback=cpython", "--emit", "ir", "-o", str(tmp_path / "o"), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    ir = (tmp_path / "o").read_text(encoding="utf-8")
    assert '"helper.twice"' in ir
    assert "islandSource" not in ir
