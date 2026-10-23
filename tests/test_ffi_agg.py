"""F1c: typed list and dict returns for manifest functions (issue #236).

The manifest grammar takes `list[T]`, `dict[K, V]` and the sqlite row shape
`list[dict[K, V]]`; `type_ir` maps them onto the subset's elem/key/value
forms, so a returned aggregate is a regular subset value the lowerer and
emitter already know.

This file covers the parts that need a real binary: the fixture thunk
(tests/fixtures/ffi/ps_ffi_ffx_agg.c) builds a `PsSeq` of `PsBuf` strings
and a `PsMap` of str to str with the ps_rt.h helpers, and the compiled
program iterates and subscripts them like any subset list or dict. The
values stay owned by the caller; the runtime frees them on the normal path,
so the sanitizer build must run clean.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pyscriptc.emit.llvm import emit_llvm, link_binary
from pyscriptc.ffi_manifest import load_manifest
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import ffi_modules

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ffi"
MANIFEST = FIXTURE / "ffx-agg.toml"
CFILE = FIXTURE / "ps_ffi_ffx_agg.c"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _manifest():
    return load_manifest(MANIFEST)


def _lower(source: str):
    manifest = _manifest()
    with ffi_modules([manifest]):
        return lower_source(source, "ffx_agg_demo.py"), manifest


def _build(tmp_path: Path, source: str, *, sanitize: list[str] | None = None) -> Path:
    """Lower, emit and link one aggregate fixture program."""
    module, _manifest_ = _lower(source)
    assert module["fences"] == [], module["fences"]
    ll = tmp_path / "program.ll"
    ll.write_text(emit_llvm(module, standalone=True), encoding="utf-8")
    binary = tmp_path / "program"
    link_binary(ll, binary, compile_units=[CFILE], sanitize=sanitize)
    return binary


def _run(binary: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(binary)], capture_output=True, text=True, check=False)


def test_lowered_list_return_rides_the_ptr_abi_path() -> None:
    """A `list[str]` return lowers to the ptr-return ABI shape."""
    source = "import ffx\n\ndef main() -> int:\n    names: list[str] = ffx.names()\n    return len(names)\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    assign = module["functions"][0]["body"][0]
    assert assign["value"]["kind"] == "call"
    assert assign["value"]["type"] == {"kind": "list", "elem": {"kind": "str"}}


def test_lowered_dict_return_keeps_the_key_value_types() -> None:
    """A `dict[str, str]` return carries key and value into the IR node."""
    source = "import ffx\n\ndef main() -> str:\n    row: dict[str, str] = ffx.row()\n    return row['name']\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    assign = module["functions"][0]["body"][0]
    assert assign["value"]["kind"] == "call"
    assert assign["value"]["type"] == {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "str"}}
    read = module["functions"][0]["body"][1]
    assert read["kind"] == "return" and read["value"]["kind"] == "subscript"
    assert read["value"]["type"] == {"kind": "str"}


@needs_clang
def test_returned_list_iterates_and_matches_cpython(tmp_path: Path) -> None:
    """`for name in names()` prints the thunk's entries in insertion order."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> None:\n"
        "    names: list[str] = ffx.names()\n"
        "    for name in names:\n"
        "        print(name)\n"
        "    print(len(names))\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    binary = _build(tmp_path, source)
    proc = _run(binary)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "alpha\nbeta\ngamma\n3\n"


@needs_clang
def test_returned_dict_subscripts_and_matches_cpython(tmp_path: Path) -> None:
    """`row['name']` reads the thunk's keys like a subset dict."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> None:\n"
        "    row: dict[str, str] = ffx.row()\n"
        "    print(row['name'])\n"
        "    print(row['city'])\n"
        "    print(len(row))\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    binary = _build(tmp_path, source)
    proc = _run(binary)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ada\nlyon\n2\n"


@needs_clang
def test_aggregates_survive_the_sanitizer(tmp_path: Path) -> None:
    """The list and the dict leak nothing under ASan and UBSan: the runtime
    owns them from the return on, and the library never frees them."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    names: list[str] = ffx.names()\n"
        "    row: dict[str, str] = ffx.row()\n"
        "    total: int = 0\n"
        "    for name in names:\n"
        "        total = total + len(name)\n"
        "    return total + len(row['city'])\n"
    )
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = _run(binary)
    # alpha(5) + beta(4) + gamma(5) + len('lyon')(4) = 18, matching CPython.
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "18\n"
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr
    assert "runtime error" not in proc.stderr


@needs_clang
def test_row_list_shape_round_trips(tmp_path: Path) -> None:
    """`list[dict[str, str]]`, the sqlite row shape, iterates and subscripts."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> None:\n"
        "    rows: list[dict[str, str]] = ffx.rows()\n"
        "    for row in rows:\n"
        "        print(row['name'])\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )
    binary = _build(tmp_path, source)
    proc = _run(binary)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ada\nbo\n"
