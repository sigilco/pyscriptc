# SPDX-License-Identifier: Apache-2.0
"""The `unicodedata` surface over the whole code-point range (issue #476).

The S4 slice replaced the Latin-1-only table with the generated
full-range tables: the general category, the combining class, the
bidirectional class, the east asian width, the mirrored flag, the
decimal/digit/numeric values, the decompositions, the names, the formal
aliases and the four normalization forms.

The tests here replay the committed probe
`probes/differential/unicodedata_codepoints.py` against CPython byte for
byte, and then pin the boundaries a probe cannot feed: the astral plane,
the algorithmic names, the exact rational numeric values, and the error
types CPython raises.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from tests.test_gates import ROOT

CC = shutil.which("clang") or shutil.which("cc") or shutil.which("zig")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler on PATH")


def _env() -> dict[str, str]:
    import os

    return {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, else the first 3.14 on PATH, else this one."""
    pinned = sorted((Path.home() / ".local/share/uv/python").glob("cpython-3.14.*-*/bin/python3"))
    if pinned:
        return str(pinned[-1])
    return shutil.which("python3.14") or sys.executable


def _build(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / f"{name}.bin"
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "bin", "-o", str(binary)],
        cwd=ROOT,
        env=_env(),
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return binary


def _run(cmd: list[str], cwd: Path) -> tuple[bytes, bytes, int]:
    run = subprocess.run(cmd, capture_output=True, timeout=120, cwd=cwd)
    return run.stdout, run.stderr, run.returncode


def _both(tmp_path: Path, name: str, source: str) -> tuple[tuple[bytes, bytes, int], tuple[bytes, bytes, int]]:
    """Run the compiled program and CPython on one source: (native, oracle)."""
    binary = _build(tmp_path, name, source)
    native_dir = tmp_path / "native"
    oracle_dir = tmp_path / "oracle"
    native_dir.mkdir(exist_ok=True)
    oracle_dir.mkdir(exist_ok=True)
    native = _run([str(binary)], native_dir)
    oracle = _run([_oracle(), str(tmp_path / f"{name}.py")], oracle_dir)
    return native, oracle


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """`probes/differential/unicodedata_codepoints.py` is byte-identical to CPython."""
    source = (ROOT / "probes" / "differential" / "unicodedata_codepoints.py").read_text(encoding="utf-8")
    native, oracle = _both(tmp_path, "unicodedata_codepoints", source)
    assert native == oracle


def test_the_astral_plane_is_answered(tmp_path: Path) -> None:
    """A code point above U+FFFF has a name, a category and a decomposition."""
    source = (
        "import unicodedata\n"
        'print(unicodedata.category("\\U0001f600"))\n'
        'print(unicodedata.name("\\U0001f600"))\n'
        'print(unicodedata.east_asian_width("\\U0001f600"))\n'
    )
    native, oracle = _both(tmp_path, "astral", source)
    assert native == oracle
    assert b"So\n" in native[0]
    assert b"GRINNING FACE" in native[0]


def test_the_algorithmic_names_resolve_both_ways(tmp_path: Path) -> None:
    """The Hangul syllables and the CJK ideographs name themselves by arithmetic."""
    source = (
        "import unicodedata\n"
        'print(unicodedata.name("\\uac00"))\n'
        'print(unicodedata.name("\\u4e00"))\n'
        'print(unicodedata.lookup("HANGUL SYLLABLE GA"))\n'
        'print(unicodedata.lookup("CJK UNIFIED IDEOGRAPH-4E00"))\n'
    )
    native, oracle = _both(tmp_path, "algorithmic", source)
    assert native == oracle
    assert b"HANGUL SYLLABLE GA" in native[0]
    assert b"CJK UNIFIED IDEOGRAPH-4E00" in native[0]


def test_the_numeric_values_are_exact(tmp_path: Path) -> None:
    """The numeric values that no binary fraction holds keep their exact value."""
    source = (
        "import unicodedata\n"
        'print(unicodedata.numeric("\\u00bc"))\n'
        'print(unicodedata.numeric("\\u215b"))\n'
        'print(unicodedata.numeric("\\u2150"))\n'
    )
    native, oracle = _both(tmp_path, "numeric", source)
    assert native == oracle
    assert b"0.25" in native[0]


def test_the_lookup_raises_the_cpython_key_error(tmp_path: Path) -> None:
    """`unicodedata.lookup` raises a KeyError, not a ValueError."""
    source = (
        "import unicodedata\n"
        "try:\n"
        '    print(unicodedata.lookup("NOT A NAME"))\n'
        "except KeyError as exc:\n"
        '    print("KeyError:", exc)\n'
    )
    native, oracle = _both(tmp_path, "lookup_keyerror", source)
    assert native == oracle
    assert b"KeyError" in native[0]


def test_the_generated_tables_answer_the_whole_range() -> None:
    """The generated data file carries the tables for every code point.

    The generator is the oracle: it derives every table from CPython's
    own `unicodedata`, and the runtime reads what the generator wrote.
    The check here is that the tables are in place and that the version
    the runtime reports is the version the generator stamped.
    """
    data = (ROOT / "src" / "pyscriptc" / "runtime" / "ps_unicode_data.c").read_text(encoding="utf-8")
    assert 'PS_UNICODE_VERSION "16.0.0"' in data
    assert "ps_uni_cat[]" in data
    assert "ps_uni_name[]" in data
    assert "ps_uni_alias[]" in data
    assert "ps_uni_cjk[]" in data
    assert unicodedata.unidata_version == "16.0.0"
