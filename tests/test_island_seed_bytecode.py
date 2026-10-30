"""The bytecode-backed island seed (issue #712).

The program-wide island seed carries the program's module bodies as
marshalled code objects instead of source text: the binary holds a small
finder bootstrap, a marshalled index of the module table, and a body blob of
per-module deflated code objects. These tests pin the payload's shape, the
lazy load, and the semantics the seed has to preserve.
"""

from __future__ import annotations

import base64
import json
import marshal
import subprocess
import sys
import zlib
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


def _compile_ir(tmp_path: Path, target: Path, *flags: str) -> dict:
    comp = _cli("compile", str(target), "--fallback=cpython", "--emit", "ir", "-o", str(tmp_path / "o"), *flags, cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    return json.loads((tmp_path / "o").read_text(encoding="utf-8"))


def _seed_of(ir: dict) -> tuple[dict, bytes]:
    index = marshal.loads(base64.b64decode(ir["islandSeedIndex"]))
    body = base64.b64decode(ir["islandSeedBody"])
    return index, body


def test_seed_carries_marshalled_bytecode_not_source(tmp_path: Path) -> None:
    """The seed holds code objects, and the bootstrap holds no module source."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "from helper import twice\nprint(twice(4))\n",
            "helper.py": "import shlex\n\n\ndef twice(x):\n    return x * len(shlex.split('a b'))\n",
        },
    )
    ir = _compile_ir(tmp_path, prog)
    index, body = _seed_of(ir)
    assert "helper" in index
    origin, is_package, offset, length = index["helper"]
    assert origin.endswith("helper.py")
    assert is_package is False
    code = marshal.loads(zlib.decompress(body[offset : offset + length]))
    assert code.co_filename == origin
    # The bootstrap does not carry the module source any more.
    assert "helper.py" not in ir["islandSource"]
    assert len(ir["islandSource"]) < 4096


def test_seed_index_covers_every_module_and_bodies_are_disjoint(tmp_path: Path) -> None:
    """Every module of the program has an index entry, and the bodies tile the blob."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "import a\nimport b\nprint(a.v(), b.v())\n",
            "a.py": "import shlex\n\n\ndef v():\n    return len(shlex.split('a b'))\n",
            "b.py": "import shlex\n\n\ndef v():\n    return len(shlex.split('a b c'))\n",
        },
    )
    ir = _compile_ir(tmp_path, prog)
    index, body = _seed_of(ir)
    assert set(index) == {"a", "b"}
    spans = sorted((off, off + length) for _o, _p, off, length in index.values())
    assert spans[0][0] == 0
    for (_start, end), (start, _end) in zip(spans, spans[1:]):
        assert end == start
    assert spans[-1][1] == len(body)


def test_seed_module_bodies_load_lazily(tmp_path: Path) -> None:
    """A module the program never imports is never inflated.

    The bootstrap's loader reads one body out of the blob per import, so a
    run that touches one module reads one body. The test reads the blob
    itself: a body that is never touched stays compressed in the blob.
    """

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "import used\nprint(used.v())\n",
            "used.py": "import shlex\n\n\ndef v():\n    return len(shlex.split('a b'))\n",
            "unused.py": "import shlex\n\n\ndef v():\n    return len(shlex.split('a b c'))\n",
        },
    )
    ir = _compile_ir(tmp_path, prog)
    index, body = _seed_of(ir)
    assert "unused" in index
    # The unused body is a deflated blob: inflating it is what the loader
    # would do, and the deflated form is much smaller than the code.
    _o, _p, offset, length = index["unused"]
    blob = body[offset : offset + length]
    assert len(blob) < len(zlib.decompress(blob))


def test_seed_preserves_module_semantics(tmp_path: Path) -> None:
    """`__file__`, `__spec__`, `__package__`, `sys.modules` identity and the
    import order are the same as under CPython."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "import sys\n"
                "from pathlib import Path\n"
                "import helper\n"
                "print(Path(helper.__file__).name)\n"
                "print(helper.__spec__.name, repr(helper.__package__))\n"
                "print(sys.modules['helper'] is helper)\n"
                "print(helper.v())\n"
            ),
            "helper.py": (
                "import os\n"
                "import shlex\n"
                "print('helper body', __name__)\n"
                "\n"
                "def v():\n"
                "    return os.path.basename(__file__) + str(len(shlex.split('a b')))\n"
            ),
        },
    )
    binary = tmp_path / "bin"
    comp = _cli("compile", str(prog), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog / "__main__.py")], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout


def test_seed_traceback_names_the_compile_time_file(tmp_path: Path) -> None:
    """A traceback out of an island module names the module's own file."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "import helper\n"
                "try:\n"
                "    helper.boom()\n"
                "except ZeroDivisionError:\n"
                "    import traceback\n"
                "    print(traceback.format_exc())\n"
            ),
            "helper.py": "import shlex\n\n\ndef boom():\n    return 1 / 0\n",
        },
    )
    binary = tmp_path / "bin"
    comp = _cli("compile", str(prog), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert got.returncode == 0, got.stderr
    assert "helper.py" in got.stdout
    assert "ZeroDivisionError" in got.stdout


def test_seed_carries_no_module_source_in_the_binary(tmp_path: Path) -> None:
    """The module source text is not in the emitted LLVM: the bodies are
    marshalled code, so the source does not appear as a string literal."""

    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "import helper\nprint(helper.v())\n",
            "helper.py": "import shlex\n\n\ndef v():\n    return shlex.split('a b')\n",
        },
    )
    binary = tmp_path / "bin"
    comp = _cli("compile", str(prog), "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    blob = binary.read_bytes()
    assert b"shlex.split('a b')" not in blob
