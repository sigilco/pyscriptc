"""The pyscriptc-orjson pack's C thunk against the real orjson under CPython.

The host harness (packaging/adapters/pyscriptc-orjson/tests/host_main.c) runs
the pack's conformance sequences through the pack's thunk, the C over the
runtime's json writer/reader the compiler lowers orjson calls onto. This test
builds that harness the way test_ffi_sqlite_adapter.py builds the sqlite one
and compares the output byte for byte against the real orjson under this
interpreter, which is the receipt the conformance runner (#378) will automate
per case.

Nothing here touches the compiler's lowering or link path; the compiled
surface stays pinned by tests/test_orjson_pack.py.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "packaging" / "adapters" / "pyscriptc-orjson"
RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"


def _harness_stdout() -> str:
    """Build and run the host harness, return its stdout verbatim."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no host C compiler")
    strict = ["-O1", "-Wall", "-Werror", f"-I{RUNTIME}"]
    units = [(PACK / "src" / "ps_pack_orjson.c", strict), (PACK / "tests" / "host_main.c", strict)]
    units += [(path, ["-O1", "-w"]) for path in sorted(RUNTIME.glob("*.c")) if path.name != "ps_island.c"]
    with tempfile.TemporaryDirectory(prefix="pyscriptc-orjson-") as tmp:
        objects: list[str] = []
        for index, (src, flags) in enumerate(units):
            obj = Path(tmp) / f"{index}_{src.stem}.o"
            done = subprocess.run(
                [cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False
            )
            assert done.returncode == 0, f"{src.name}: {done.stderr}"
            objects.append(str(obj))
        binary = Path(tmp) / "host_orjson"
        link = subprocess.run([cc, *objects, "-lm", "-o", str(binary)], capture_output=True, text=True, check=False)
        assert link.returncode == 0, link.stderr
        ran = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
        assert ran.returncode == 0, ran.stderr
        return ran.stdout


def _orjson_stdout() -> str:
    """The same sequences against the real orjson under this interpreter.

    The indented dumps output carries embedded newlines, so the reference
    keeps them and the comparison is byte for byte.
    """
    orjson = pytest.importorskip("orjson")
    dumps = orjson.dumps
    loads = orjson.loads
    lines = [
        "dumps_compact: " + dumps({"a": 1, "b": 2}).decode(),
        "dumps_sorted: " + dumps({"a": 1, "b": 2}, option=orjson.OPT_SORT_KEYS).decode(),
        "dumps_indent: " + dumps({"a": 1, "b": 2}, option=orjson.OPT_INDENT_2).decode(),
        "dumps_both_options: " + dumps({"a": 1, "b": 2}, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2).decode(),
        f"loads_str: {loads('{"a": 41}')['a']}",
        f"loads_bytes: {loads(b'{"a": 40}')['a']}",
    ]
    document = {"k": [10, 20]}
    lines.append("round_trip: " + dumps(document).decode())
    value = loads(dumps(document))
    lines.append(f"round_trip_sum: {value['k'][0] + value['k'][1]}")
    return "\n".join(lines) + "\n"


def test_thunk_matches_cpython_orjson() -> None:
    assert _harness_stdout() == _orjson_stdout()
