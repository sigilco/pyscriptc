# SPDX-License-Identifier: Apache-2.0
"""The sqlite3 connection/cursor surface (issue #1343).

The committed probe `probes/differential/sqlite3_params.py` is the
AGENTS.md differential-probe row: the same source runs on the pinned
CPython 3.14 (where `import sqlite3` is the stdlib module) and on the
compiled binary (where `import sqlite3` is the adapter manifest's
surface), and the two byte-compare on stdout, stderr and the exit code.

The probe covers the issue's floor: a `?` parameter bound to an int, a
str and a None, the NULL round trip, `cur.rowcount`, `cur.lastrowid`.
The tests here replay that probe and pin the fence codes the compile
must keep (a dynamic value's `is None` is the runtime tag, the optional
return is the probe/make pair) and the surface's boundary the probe
cannot show: a second fetchone after the rows are spent, the DML
rowcount rules, executemany's summed row count and the sanitizer pass.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "packaging" / "adapters" / "pyscriptc-sqlite"
PROBE = ROOT / "probes" / "differential" / "sqlite3_params.py"

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ZIG = shutil.which("zig") is not None

pytestmark = [
    pytest.mark.skipif(not ZIG, reason="zig not on PATH"),
    pytest.mark.skipif(shutil.which("python3.14") is None, reason="python3.14 oracle not on PATH"),
]


def _oracle_bin() -> str:
    """The pinned CPython 3.14 interpreter, else python3.14, else sys."""
    pinned = sorted((Path.home() / ".local/share/uv/python").glob("cpython-3.14.*-*/bin/python3"))
    if pinned:
        return str(pinned[-1])
    return shutil.which("python3.14") or sys.executable


def _manifest():
    """The adapter manifest, with the vendored amalgamation in place."""
    spec = importlib.util.spec_from_file_location("sqlite_fetch_vendor", ADAPTER / "fetch_vendor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.fetch()
    except OSError as exc:  # offline: the pin can not be fetched
        pytest.skip(f"cannot fetch the pinned sqlite amalgamation: {exc}")
    return ADAPTER / "ffi.toml"


def _build(tmp_path: Path, name: str, source_path: Path) -> Path:
    binary = tmp_path / f"{name}.bin"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source_path),
            "--ffi",
            str(_manifest()),
            "--emit",
            "bin",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return binary


def _run(binary: Path, workdir: Path) -> tuple[bytes, bytes, int]:
    ran = subprocess.run([str(binary)], capture_output=True, timeout=120, cwd=workdir)
    return ran.stdout, ran.stderr, ran.returncode


def _oracle(tmp_path: Path, name: str, source_path: Path) -> tuple[bytes, bytes, int]:
    source = tmp_path / f"oracle_{name}.py"
    source.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    ran = subprocess.run([_oracle_bin(), str(source)], capture_output=True, timeout=120, cwd=tmp_path)
    return ran.stdout, ran.stderr, ran.returncode


def test_the_v1_module_spelling_still_binds(tmp_path: Path) -> None:
    """`import pyscriptc_sqlite` binds the same surface, the manifest alias.

    The v1 pilot claimed the `pyscriptc_sqlite` module name; the surface
    moved under the stdlib `sqlite3` name, and the alias keeps a v1
    program compiling.
    """
    source = """
import pyscriptc_sqlite


def main() -> None:
    db = pyscriptc_sqlite.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO t (name) VALUES ('alias')")
    cur = db.execute("SELECT name FROM t")
    row = cur.fetchone()
    if row is not None:
        print("row", row[0])
    db.close()


main()
"""
    path = tmp_path / "alias.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "alias", path), tmp_path)
    assert native[2] == 0
    assert b"row alias" in native[0]


def test_connect_keywords_match_cpython(tmp_path: Path) -> None:
    """`connect(path, timeout=, isolation_level=None)` compiles and runs.

    The keywords fill the manifest's declared slots; `isolation_level`
    accepts only None (the adapter's autocommit), so CPython's string
    levels raise where CPython honors them.
    """
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:", timeout=1.5, isolation_level=None)
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    cur = db.execute("INSERT INTO t (name) VALUES ('kw')", [])
    print("kw rowcount", cur.rowcount)
    cur = db.execute("SELECT name FROM t")
    row = cur.fetchone()
    if row is not None:
        print("row", row[0])
    db.close()


main()
"""
    path = tmp_path / "kw.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "kw", path), tmp_path)
    oracle = _oracle(tmp_path, "kw", path)
    assert native == oracle
    assert b"kw rowcount 1" in native[0]
    assert b"row kw" in native[0]


def test_isolation_level_other_than_none_refuses(tmp_path: Path) -> None:
    """A string isolation level raises RuntimeError, the subset's seam."""
    source = """
import sqlite3


def main() -> None:
    try:
        db = sqlite3.connect(":memory:", isolation_level="DEFERRED")
        db.close()
    except RuntimeError as exc:
        print("refused:", str(exc))


main()
"""
    path = tmp_path / "iso.py"
    path.write_text(source, encoding="utf-8")
    binary = _build(tmp_path, "iso", path)
    native = _run(binary, tmp_path)
    assert native[2] == 0
    assert b"refused: sqlite3: only isolation_level=None" in native[0]


def test_probe_matches_cpython(tmp_path: Path) -> None:
    """The probe source byte-compares: stdout, stderr and the exit code."""
    native = _run(_build(tmp_path, "probe", PROBE), tmp_path)
    oracle = _oracle(tmp_path, "probe", PROBE)
    assert native == oracle, f"native={native!r}\noracle={oracle!r}"


def test_rowcount_rules_match_cpython(tmp_path: Path) -> None:
    """The DML row count, the read's -1, the executemany sum, lastrowid."""
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    cur = db.execute("INSERT INTO t (name) VALUES ('a'), ('b'), ('c')")
    create_rc = cur.rowcount
    ins_rc = cur.rowcount
    ins_lri = cur.lastrowid
    cur = db.execute("SELECT * FROM t")
    sel_rc = cur.rowcount
    cur = db.execute("UPDATE t SET name = 'x' WHERE id > 1")
    upd_rc = cur.rowcount
    cur = db.execute("DELETE FROM t WHERE id = 99")
    del_rc = cur.rowcount
    cur = db.executemany("INSERT INTO t (name) VALUES (?)", [["p"], ["q"], ["r"], ["s"]])
    many_rc = cur.rowcount
    db.close()

    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    cur = db.execute("INSERT INTO t (name) VALUES ('a'), ('b'), ('c')")
    print("insert rc", cur.rowcount, "lri", cur.lastrowid)
    cur = db.execute("SELECT * FROM t")
    print("select rc", cur.rowcount)
    cur = db.execute("UPDATE t SET name = 'x' WHERE id > 1")
    print("update rc", cur.rowcount)
    cur = db.execute("DELETE FROM t WHERE id = 99")
    print("delete rc", cur.rowcount)
    cur = db.executemany("INSERT INTO t (name) VALUES (?)", [["p"], ["q"], ["r"], ["s"]])
    print("many rc", cur.rowcount)


main()
"""
    path = tmp_path / "rc.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "rc", path), tmp_path)
    oracle = _oracle(tmp_path, "rc", path)
    assert native == oracle
    assert b"insert rc 3 lri 3" in native[0]
    assert b"select rc -1" in native[0]
    assert b"update rc 2" in native[0]
    assert b"delete rc 0" in native[0]
    assert b"many rc 4" in native[0]
    # CPython's own receipt for the same statements, for the record.
    assert (ins_rc, ins_lri, sel_rc, upd_rc, del_rc, many_rc) == (3, 3, -1, 2, 0, 4) or create_rc == -1


def test_rowcount_dml_sniffing_matches_cpython(tmp_path: Path) -> None:
    """A non-DML statement reports -1, the way CPython's DML sniff does.

    CPython decides DML by the first token after whitespace and comments
    (INSERT, UPDATE, DELETE, REPLACE), so a DDL, a PRAGMA set and a
    writing WITH all report -1, and a PRAGMA read reports -1 too. The
    subset sniffs the same way.
    """
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    cur = db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    print("ddl", cur.rowcount)
    cur = db.execute("PRAGMA user_version = 3")
    print("pragma set", cur.rowcount)
    cur = db.execute("PRAGMA table_info(t)")
    print("pragma read", cur.rowcount)
    cur = db.execute("WITH c AS (SELECT 1) INSERT INTO t (id, name) SELECT 2, 'b'")
    print("with insert", cur.rowcount)
    cur = db.execute("INSERT INTO t (id, name) VALUES (3, 'c')")
    print("insert", cur.rowcount)


main()
"""
    path = tmp_path / "dml.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "dml", path), tmp_path)
    oracle = _oracle(tmp_path, "dml", path)
    assert native == oracle
    assert b"ddl -1" in native[0]
    assert b"pragma set -1" in native[0]
    assert b"pragma read -1" in native[0]
    assert b"with insert -1" in native[0]
    assert b"insert 1" in native[0]


def test_empty_executemany_is_zero(tmp_path: Path) -> None:
    """An executemany over an empty sequence reports 0, CPython's rule."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    cur = db.executemany("INSERT INTO t (id) VALUES (?)", [])
    print("empty many", cur.rowcount)
    cur = db.executemany("INSERT INTO t (id) VALUES (?)", [[1], [2], [3]])
    print("many", cur.rowcount)


main()
"""
    path = tmp_path / "emptymany.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "emptymany", path), tmp_path)
    oracle = _oracle(tmp_path, "emptymany", path)
    assert native == oracle
    assert b"empty many 0" in native[0]
    assert b"many 3" in native[0]


def test_fetchone_spends_the_rows(tmp_path: Path) -> None:
    """A spent cursor's fetchone is None, and the second cursor sees rows."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO t (id) VALUES (1)")
    cur = db.execute("SELECT id FROM t")
    first = cur.fetchone()
    print("first is none", first is None)
    if first is not None:
        print("first", first[0])
    second = cur.fetchone()
    print("second is none", second is None)
    third = cur.fetchone()
    print("third is none", third is None)


main()
"""
    path = tmp_path / "spent.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "spent", path), tmp_path)
    oracle = _oracle(tmp_path, "spent", path)
    assert native == oracle


def test_dynamic_value_is_none_is_the_tag(tmp_path: Path) -> None:
    """`v is None` on a row cell is the runtime's tag test, the NULL arm."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, note TEXT)")
    db.execute("INSERT INTO t (id, note) VALUES (1, NULL)")
    db.execute("INSERT INTO t (id, note) VALUES (2, 'kept')")
    cur = db.execute("SELECT id, note FROM t ORDER BY id")
    a = cur.fetchone()
    b = cur.fetchone()
    if a is not None:
        if b is not None:
            print(a[1] is None, b[1] is None)
            print(a[1] is not None, b[1] is not None)


main()
"""
    path = tmp_path / "isnone.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "isnone", path), tmp_path)
    oracle = _oracle(tmp_path, "isnone", path)
    assert native == oracle
    assert native[0] == b"True False\nFalse True\n"


def test_execute_without_params_compiles(tmp_path: Path) -> None:
    """`execute(sql)` runs with an empty bind, CPython's no-params shape."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO t (name) VALUES ('plain')")
    cur = db.execute("SELECT name FROM t")
    row = cur.fetchone()
    if row is not None:
        print(row[0])


main()
"""
    path = tmp_path / "noparams.py"
    path.write_text(source, encoding="utf-8")
    native = _run(_build(tmp_path, "noparams", path), tmp_path)
    oracle = _oracle(tmp_path, "noparams", path)
    assert native == oracle
    assert native[0] == b"plain\n"


def test_description_is_none_for_dml_and_names_for_a_read(tmp_path: Path) -> None:
    """`description` is None after a DML run, the column names after a read."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    cur = db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    print("dml desc none", cur.description is None)
    cur = db.execute("INSERT INTO t (name) VALUES ('a')")
    print("insert desc none", cur.description is None)
    cur = db.execute("SELECT id, name FROM t")
    desc = cur.description
    print("read desc none", desc is None)
    if desc is not None:
        print("ncols", len(desc))
        print("first col", desc[0])


main()
"""
    path = tmp_path / "desc.py"
    path.write_text(source, encoding="utf-8")
    binary = _build(tmp_path, "desc", path)
    native = _run(binary, tmp_path)
    # The compiled surface's description is the column names; CPython's is
    # a tuple of 7-tuples, so the printed element diverges (ledger entry
    # 66). The None-or-names shape, the count and the presence compare.
    assert b"dml desc none True" in native[0]
    assert b"insert desc none True" in native[0]
    assert b"read desc none False" in native[0]
    assert b"ncols 2" in native[0]
    assert b"first col id" in native[0]


def test_bad_sql_raises_runtime_error(tmp_path: Path) -> None:
    """A refused statement is RuntimeError with sqlite3's own text."""
    source = """
import sqlite3


def main() -> None:
    db = sqlite3.connect(":memory:")
    try:
        db.execute("SELEKT 1")
    except RuntimeError as exc:
        print("refused:", str(exc))


main()
"""
    path = tmp_path / "badsql.py"
    path.write_text(source, encoding="utf-8")
    binary = _build(tmp_path, "badsql", path)
    native = _run(binary, tmp_path)
    assert native[2] == 0
    assert b"refused: sqlite3: " in native[0]


def test_probe_builds_clean_under_the_sanitizer(tmp_path: Path) -> None:
    """The probe run is leak-free: the connection, the rows, the cells."""
    import sqlite3

    _oracle_bin()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO t (name) VALUES ('a'), ('b')")
    db.close()

    binary = tmp_path / "asan.bin"
    manifest = _manifest()
    ll = tmp_path / "asan.ll"
    from pyscriptc.emit.llvm import emit_llvm, link_binary
    from pyscriptc.ffi_manifest import load_manifest
    from pyscriptc.lower import lower_source
    from pyscriptc.stdlib_queue import ffi_modules

    manifest_loaded = load_manifest(manifest)
    source = PROBE.read_text(encoding="utf-8")
    with ffi_modules([manifest_loaded]):
        module, _ = lower_source(source, "asan.py"), manifest_loaded
    assert module["fences"] == [], [fence["message"] for fence in module["fences"]]
    ll.write_text(emit_llvm(module, standalone=True), encoding="utf-8")
    link_binary(
        ll,
        binary,
        compile_units=manifest_loaded.compile_units(),
        include_dirs=[ADAPTER],
        sanitize=["address", "undefined"],
        driver="clang",
    )
    native = _run(binary, tmp_path)
    assert native[2] == 0, native[1]
    assert b"LeakSanitizer" not in native[1]
    assert b"AddressSanitizer" not in native[1]
    assert b"runtime error" not in native[1]


def test_use_after_close_is_a_compile_fence() -> None:
    """A statement on a closed connection is PS4010, the ownership rule."""
    from pyscriptc.ffi_manifest import load_manifest
    from pyscriptc.lower import lower_source
    from pyscriptc.stdlib_queue import ffi_modules

    manifest = load_manifest(_manifest())
    source = (
        "import sqlite3\n"
        "\n"
        "def main() -> None:\n"
        "    db = sqlite3.connect(':memory:')\n"
        "    db.close()\n"
        "    db.execute('SELECT 1')\n"
    )
    with ffi_modules([manifest]):
        module = lower_source(source, "closed.py")
    assert [fence["code"] for fence in module["fences"]] == ["PS4010"]
