"""Program-wide island for multi-module programs (Refs #641).

Under `--fallback=cpython` a program directory with any unit on the island
runs whole island-side through an in-memory finder, so imports, relative
imports, cycles, `__name__`, `__file__` and import order are CPython's own.
Every case diffs the compiled binary against CPython.
"""

from __future__ import annotations

import os
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


def test_package_program_fenced_imported_module(tmp_path: Path) -> None:
    """The original #641 repro: an imported module of a package-as-program
    holds a fenced function (`os.walk`), and the entry calls it."""
    prog = tmp_path / "mp"
    _write(
        prog,
        {
            "__init__.py": "",
            "helper.py": (
                "import os\n\n\ndef count(d: str) -> int:\n"
                "    n = 0\n    for root, dirs, files in os.walk(d):\n"
                "        n += len(files)\n    return n\n"
            ),
            "__main__.py": "from mp.helper import count\nimport os\nprint(count('data'))\n",
        },
    )
    # A fixed payload directory inside the tree: the count is deterministic
    # (a `__pycache__` next to the modules would race the two runs).
    (prog / "data").mkdir()
    (prog / "data" / "a.txt").write_text("x", encoding="utf-8")
    (prog / "data" / "b.txt").write_text("y", encoding="utf-8")
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, "-m", "mp"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout


def test_program_typed_cycle_matches_cpython(tmp_path: Path) -> None:
    """A typed import cycle in a program runs island-side in CPython's order."""
    prog = tmp_path / "cyc"
    _write(
        prog,
        {
            "__init__.py": "",
            "a.py": "import cyc.b\n\nprint('init a')\n\n\ndef one() -> int:\n    return 1\n",
            "b.py": "from cyc import a\n\nprint('init b')\n",
            "__main__.py": "import cyc.a\nprint(cyc.a.one())\n",
        },
    )
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, "-m", "cyc"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout


def test_program_non_constant_module_assignment(tmp_path: Path) -> None:
    """A module-level `Path(os.environ.get(...))` in an imported module runs
    island-side (issue #641 comment): native keeps refusing, the fallback
    tier routes the assignment to the island."""
    prog = tmp_path / "env"
    _write(
        prog,
        {
            "__init__.py": "",
            "config.py": (
                "import os\nfrom pathlib import Path\n\n"
                "CONFIG_PATH = Path(os.environ.get('PYSC_PROG641', '.')).expanduser()\n"
            ),
            "__main__.py": "from env.config import CONFIG_PATH\nprint(CONFIG_PATH)\n",
        },
    )
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    env = {**os.environ, "PYSC_PROG641": "/env641"}
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
    want = subprocess.run(
        [sys.executable, "-m", "env"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=env,
    )
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout == "/env641\n"


def test_library_import_of_island_definition_refuses(tmp_path: Path) -> None:
    """A package (no --program) importing another module's island definition
    refuses with a named PS2002 instead of crashing the emitter (issue #641)."""
    pkg = tmp_path / "pkg"
    _write(
        pkg,
        {
            "__init__.py": "",
            "helper.py": (
                "import os\n\n\ndef count(d: str) -> int:\n"
                "    n = 0\n    for root, dirs, files in os.walk(d):\n"
                "        n += len(files)\n    return n\n"
            ),
            "cli.py": "from pkg.helper import count\n\n\ndef go(d: str) -> int:\n    return count(d)\n",
        },
    )
    comp = _cli("compile", str(pkg), "--fallback=cpython", "--emit", "bin", "-o", str(tmp_path / "bin"), cwd=tmp_path)
    assert comp.returncode == 3, comp.stderr
    assert "PS2002" in comp.stderr
    assert "pkg.helper.count" in comp.stderr
    assert "runs on the island floor of its own module" in comp.stderr
    assert "KeyError" not in comp.stderr and "Traceback" not in comp.stderr
