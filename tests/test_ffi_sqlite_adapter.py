"""The pyscriptc-sqlite adapter (F3a): manifest, .pyi surface and host thunk.

The thunk is built as a host C test against the runtime and compared line by
line with the stdlib `sqlite3` module. Nothing here touches the compiler's
lowering or link path; the end-to-end compile test lives in #66.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import sqlite3
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "pyscriptc-sqlite"
RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"
PS_RUNTIMEERROR = 41
PS_VALUEERROR = 51


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("sqlite_fetch_vendor", ADAPTER / "fetch_vendor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_pin_is_complete() -> None:
    pin = tomllib.loads((ADAPTER / "vendor" / "VENDOR.toml").read_text(encoding="utf-8"))
    for key in ("version", "url", "license", "zip_sha256", "zip_sha3_256", "sqlite3.c_sha256", "sqlite3.h_sha256"):
        assert pin[key], key
    assert pin["url"].startswith("https://sqlite.org/")
    assert "Public domain" in pin["license"]


def test_manifest_validates_under_load_manifest() -> None:
    ffi_manifest = pytest.importorskip("pyscriptc.ffi_manifest", reason="F0 parser (#182) not on this base yet")
    manifest = ffi_manifest.load_manifest(ADAPTER / "ffi.toml")
    assert manifest.module == "pyscriptc_sqlite"
    assert {fn.name for fn in manifest.functions} == {"connect", "execute", "query", "commit", "close"}
    assert {h.name for h in manifest.handles} == {"Connection"}
    assert {c.name: c.value for c in manifest.consts} == {"OK": 0, "ROW": 100, "DONE": 101}
    close = manifest.function("close")
    assert close is not None and [ref.param for ref in close.consumes] == ["db"]
    assert [p.name for p in manifest.unit_paths()] == ["sqlite3.c", "ps_ffi_sqlite.c"]


def test_pyi_matches_manifest_surface() -> None:
    manifest = tomllib.loads((ADAPTER / "ffi.toml").read_text(encoding="utf-8"))
    tree = ast.parse((ADAPTER / "pyscriptc_sqlite.pyi").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert set(funcs) == {fn["name"] for fn in manifest["fn"]}
    for fn in manifest["fn"]:
        node = funcs[fn["name"]]
        signature = "(" + ", ".join(f"{a.arg}: {ast.unparse(a.annotation)}" for a in node.args.args) + ")"
        signature += f" -> {ast.unparse(node.returns)}"
        assert signature == fn["py"], fn["name"]
    annotated = {n.target.id for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    assert annotated == {c["name"] for c in manifest["const"]}
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    assert classes == {h["name"] for h in manifest["handle"]}


def test_every_abi_symbol_is_defined_by_the_thunk() -> None:
    manifest = tomllib.loads((ADAPTER / "ffi.toml").read_text(encoding="utf-8"))
    source = (ADAPTER / "ps_ffi_sqlite.c").read_text(encoding="utf-8")
    for fn in manifest["fn"]:
        assert f" *{fn['abi']}(" in source or f" {fn['abi']}(" in source, fn["abi"]


def _expected_output() -> str:
    """The same sequence as host_main.c, run on the stdlib module."""
    lines: list[str] = []

    def rows(db: sqlite3.Connection, sql: str) -> None:
        cur = db.execute(sql)
        names = [d[0] for d in cur.description or ()]
        for row in cur.fetchall():
            cells = ["" if v is None else str(v) for v in row]
            lines.append("row" + "".join(f" {k}={v}" for k, v in zip(names, cells, strict=True)))

    def raises(label: str, fn) -> None:
        try:
            fn()
        except sqlite3.ProgrammingError as exc:
            if "one statement" in str(exc):
                lines.append(f"{label}: code={PS_RUNTIMEERROR} msg=sqlite3: {exc}")
            else:
                lines.append(f"{label}: code={PS_VALUEERROR} msg={exc}")
        except sqlite3.Error as exc:
            lines.append(f"{label}: code={PS_RUNTIMEERROR} msg=sqlite3: {exc}")
        else:
            lines.append(f"{label}: no error")

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, score REAL, note TEXT)")
    db.execute("INSERT INTO t (name, score, note) VALUES ('ada', 1.5, NULL), ('bob', 2, 'x y'), ('café', -3.25, '')")
    rows(db, "SELECT id, name, score, note FROM t ORDER BY id")
    rows(db, "SELECT count(*) AS n FROM t WHERE score > 0")
    rows(db, "UPDATE t SET score = 0 WHERE id = 1")
    db.execute("BEGIN")
    db.execute("DELETE FROM t WHERE id = 2")
    db.commit()
    lines.append("commit=0")
    db.commit()
    lines.append("commit_idle=0")
    rows(db, "SELECT id FROM t ORDER BY id")
    raises("bad_sql", lambda: db.execute("SELEKT 1"))
    raises("two_statements", lambda: db.execute("SELECT 1; SELECT 2"))
    db.close()
    db.close()
    raises("closed", lambda: db.execute("SELECT 1"))
    return "\n".join(lines) + "\n"


def test_thunk_matches_cpython_sqlite3(tmp_path: Path) -> None:
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no host C compiler")
    fetcher = _load_fetcher()
    try:
        vendor = fetcher.fetch()
    except OSError as exc:  # offline: the pin can not be fetched
        pytest.skip(f"cannot fetch the pinned sqlite amalgamation: {exc}")
    strict = ["-O1", "-Wall", "-Werror", f"-I{RUNTIME}"]
    units = [
        (vendor / "sqlite3.c", ["-O1", "-w", "-DSQLITE_THREADSAFE=0", "-DSQLITE_OMIT_LOAD_EXTENSION"]),
        (ADAPTER / "ps_ffi_sqlite.c", strict),
        (ADAPTER / "tests" / "host_main.c", strict),
    ]
    units += [(path, ["-O1", "-w"]) for path in sorted(RUNTIME.glob("*.c")) if path.name != "ps_island.c"]
    objects: list[str] = []
    for index, (src, flags) in enumerate(units):
        obj = tmp_path / f"{index}_{src.stem}.o"
        done = subprocess.run([cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{src.name}: {done.stderr}"
        objects.append(str(obj))
    binary = tmp_path / "host_sqlite"
    link = subprocess.run([cc, *objects, "-lm", "-o", str(binary)], capture_output=True, text=True, check=False)
    assert link.returncode == 0, link.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == _expected_output()
