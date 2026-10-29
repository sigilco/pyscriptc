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


def test_program_data_files_survive_relocation(tmp_path: Path) -> None:
    """Issue #665: the acceptance shape.

    A program module reads a sibling data file through
    `Path(__file__).parent`. The output is relocated and the source tree is
    renamed away (gone, as far as the run can tell), the run happens with an
    empty environment (`env -i`: no `PYSC_PROGRAM_DIR`, no `VIRTUAL_ENV`),
    and the binary must still read the data file, byte-identical to CPython.
    """

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "data.json": '{"msg": "hello-data"}\n',
            "helper.py": (
                "import json\nfrom pathlib import Path\n\n"
                "DATA_FILE = Path(__file__).parent / 'data.json'\n\n\n"
                "def read_data() -> str:\n    return json.loads(DATA_FILE.read_text())['msg']\n"
            ),
            "__main__.py": (
                "from helper import read_data\nimport os\n\n"
                "print(read_data(), os.path.basename(os.path.dirname(__file__)))\n"
            ),
        },
    )
    binary = tmp_path / "out" / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    binary.rename(relocated / "bin")
    (tmp_path / "out" / "pyscriptc-data").rename(relocated / "pyscriptc-data")
    hidden = tmp_path / "prog-gone"
    prog.rename(hidden)

    got = subprocess.run([str(relocated / "bin")], capture_output=True, text=True, timeout=60, env={})
    hidden.rename(prog)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout == "hello-data prog\n"


def test_program_data_subdirectory_survives_relocation(tmp_path: Path) -> None:
    """A `data/` subtree rides the staged root with its relative paths."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "data/sub/words.txt": "alpha beta\n",
            "reader.py": (
                "from pathlib import Path\n\n"
                "WORDS = Path(__file__).parent / 'data' / 'sub' / 'words.txt'\n\n\n"
                "def count() -> int:\n    return len(WORDS.read_text().split())\n"
            ),
            "__main__.py": "from reader import count\nprint(count())\n",
        },
    )
    binary = tmp_path / "out" / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    binary.rename(relocated / "bin")
    (tmp_path / "out" / "pyscriptc-data").rename(relocated / "pyscriptc-data")
    hidden = tmp_path / "prog-gone"
    prog.rename(hidden)

    got = subprocess.run([str(relocated / "bin")], capture_output=True, text=True, timeout=60, env={})
    assert got.returncode == 0, got.stderr
    assert got.stdout == "2\n"


def test_relocated_run_puts_no_binary_dir_on_sys_path(tmp_path: Path) -> None:
    """The `env -i` guard (issue #665): with no `PYSC_PROGRAM_DIR` and no
    `VIRTUAL_ENV`, the `argv[0]` fallback in `ps_island.c` must not put the
    binary's own `bin/` directory on `sys.path` - a relocated bundle would
    otherwise shadow stdlib names with whatever sits beside the binary."""

    prog = tmp_path / "prog"
    _write(prog, {"__main__.py": "import sys\nprint('|'.join(sys.path))\n"})
    binary = tmp_path / "out" / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    binary.rename(relocated / "bin")
    hidden = tmp_path / "prog-gone"
    prog.rename(hidden)

    got = subprocess.run([str(relocated / "bin")], capture_output=True, text=True, timeout=60, env={})
    hidden.rename(prog)
    assert got.returncode == 0, got.stderr
    entries = got.stdout.strip().split("|")
    # The binary's own directory stays off the path: a relocated bundle must
    # not shadow stdlib names with whatever sits beside the binary. The
    # bundle root (the binary's parent) is the script-directory entry, the
    # same slot CPython fills with the directory of the script it ran.
    assert str(relocated / "bin") not in entries


def test_unstaged_data_lookup_is_a_numbered_divergence(tmp_path: Path) -> None:
    """The divergence 46 pin: a program whose data files were NOT staged
    (here: the module reads through a path that leaves the program root, so
    nothing is staged) still resolves the lookup against the compile-time
    path, and a relocated run fails where CPython succeeds."""

    prog = tmp_path / "prog"
    data = tmp_path / "outside.json"
    data.write_text("7\n", encoding="utf-8")
    _write(
        prog,
        {
            "reader.py": "from pathlib import Path\n\nP = Path(__file__).parent.parent / 'outside.json'\n",
            "__main__.py": "from reader import P\nprint(P.read_text())\n",
        },
    )
    binary = tmp_path / "out" / "bin"
    comp = _cli(
        "compile", str(prog), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr

    want = subprocess.run([sys.executable, str(prog / "__main__.py")], capture_output=True, text=True, timeout=60)
    want.stdout = want.stdout.rstrip("\n") + "\n"
    assert want.returncode == 0 and want.stdout == "7\n"

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    binary.rename(relocated / "bin")
    hidden = tmp_path / "prog-gone"
    prog.rename(hidden)
    data.unlink()

    got = subprocess.run([str(relocated / "bin")], capture_output=True, text=True, timeout=60, env={})
    assert got.returncode != 0
    assert "FileNotFoundError" in got.stderr


def test_program_file_reads_sibling_data_unrelocated(tmp_path: Path) -> None:
    """The un-relocated shape stays byte-identical to CPython: staging never
    changes where the data lookup lands when the source tree is present."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "data.json": '{"msg": "here"}\n',
            "helper.py": (
                "import json\nfrom pathlib import Path\n\n"
                "DATA_FILE = Path(__file__).parent / 'data.json'\n\n\n"
                "def read_data() -> str:\n    return json.loads(DATA_FILE.read_text())['msg']\n"
            ),
            "__main__.py": "from helper import read_data\nprint(read_data())\n",
        },
    )
    out = _compare(tmp_path, prog, [sys.executable, str(prog)])
    assert out == "here\n"


def test_library_import_of_island_definition_refuses(tmp_path: Path) -> None:
    """A package (no --program) importing another module's island definition
    refuses with a named PS2002 instead of crashing the emitter (issue #641).

    The fenced call the definition holds was `os.walk` (#509 lowered it);
    `os.scandir` keeps the same shape: a call that fences inside the helper
    plans the helper for the island floor, and the cross-module import of it
    refuses."""
    pkg = tmp_path / "pkg"
    _write(
        pkg,
        {
            "__init__.py": "",
            "helper.py": (
                "import os\n\n\ndef count(d: str) -> int:\n"
                "    n = 0\n"
                "    for entry in os.scandir(d):  # type: ignore[attr-defined]\n"
                "        n += 1\n    return n\n"
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


def test_package_program_argv0_is_the_entry_path(tmp_path: Path) -> None:
    """`python -m pkg` records the entry `__main__.py` path as argv[0], so
    argparse's default `prog` is `__main__.py` (issue #765). The binary
    must not report its own path: a real CLI derives its usage line from
    argv[0], and yt-dlp's usage line names the script under CPython."""

    def build(root: Path) -> None:
        _write(
            root,
            {
                "__init__.py": "",
                "__main__.py": "import sys\nprint(sys.argv[0])\n",
                "helper.py": (
                    "import argparse\n\n\ndef fence() -> None:\n    p = argparse.ArgumentParser()\n    p.print_help()\n"
                ),
            },
        )

    pkg = tmp_path / "mp"
    build(pkg)
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(pkg), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, "-m", "mp"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout
    assert got.stdout.endswith("__main__.py\n")
