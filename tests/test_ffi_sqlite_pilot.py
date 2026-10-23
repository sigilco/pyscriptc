"""F3: the sqlite end-to-end pilot (issue #66).

The full pipeline, not the host-thunk differential (that is
`test_ffi_sqlite_adapter.py`): a fixture script with `import
pyscriptc_sqlite` lowers through `lower_file` with the adapter manifest
installed, emits, and links the vendored sqlite3.c plus the thunk through
the `[[source]]` seam of F2. The assertions are the acceptance criteria:

- open, create, insert, select, close compiles with `--ffi` and asserts
  the returned rows, matching the stdlib `sqlite3` module on the same
  sequence (the same expected text `test_ffi_sqlite_adapter.py` derives);
- the same source cross-compiles for `x86_64-linux-musl` (compile gate;
  the binary runs where a Linux host or qemu is available);
- the raise surface (bad SQL, use after close) comes back as the
  RuntimeError the adapter documents, and the sanitizer build runs clean.

The vendored amalgamation is fetched by `fetch_vendor.py` and hash-pinned;
offline hosts skip instead of fail.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from pyscriptc.emit.llvm import emit_llvm, link_binary
from pyscriptc.ffi_manifest import load_manifest
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import ffi_modules

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "packaging" / "adapters" / "pyscriptc-sqlite"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _vendor() -> Path:
    """The fetched amalgamation, or a skip when the pin cannot be fetched."""
    spec = importlib.util.spec_from_file_location("sqlite_fetch_vendor", ADAPTER / "fetch_vendor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.fetch()
    except OSError as exc:  # offline: the pin can not be fetched
        pytest.skip(f"cannot fetch the pinned sqlite amalgamation: {exc}")


def _manifest():
    """The adapter manifest, with the vendored amalgamation in place."""
    _vendor()
    return load_manifest(ADAPTER / "ffi.toml")


def _lower(source: str):
    manifest = _manifest()
    with ffi_modules([manifest]):
        return lower_source(source, "sqlite_demo.py"), manifest


def _build(tmp_path: Path, source: str, *, sanitize: list[str] | None = None, target: str | None = None) -> Path:
    """Lower, emit and link one sqlite program through the adapter manifest.

    The units are the manifest's own `[[source]]` entries (the vendored
    sqlite3.c and the thunk), so the link path is the same one a user
    compiles with `--ffi`, not a hand-assembled unit list.
    """
    module, manifest = _lower(source)
    assert module["fences"] == [], [fence["message"] for fence in module["fences"]]
    ll = tmp_path / "program.ll"
    ll.write_text(emit_llvm(module, standalone=True), encoding="utf-8")
    binary = tmp_path / "program"
    link_binary(
        ll,
        binary,
        compile_units=manifest.compile_units(),
        include_dirs=[ADAPTER],
        sanitize=sanitize,
        target=target,
        driver="zig" if target is not None else "clang",
    )
    return binary


DEMO = """import pyscriptc_sqlite


def main() -> None:
    db: pyscriptc_sqlite.Connection = pyscriptc_sqlite.connect(':memory:')
    pyscriptc_sqlite.execute(db, 'CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)')
    pyscriptc_sqlite.execute(db, "INSERT INTO t (name) VALUES ('ada'), ('bo')")
    rows: list[dict[str, str]] = pyscriptc_sqlite.query(db, 'SELECT id, name FROM t ORDER BY id')
    for row in rows:
        print(row['name'])
    pyscriptc_sqlite.close(db)


if __name__ == '__main__':
    main()
"""


SURFACE_DEMO = """import pyscriptc_sqlite


def main() -> None:
    db: pyscriptc_sqlite.Connection = pyscriptc_sqlite.connect(':memory:')
    pyscriptc_sqlite.execute(db, 'CREATE TABLE t (id INTEGER PRIMARY KEY)')
    try:
        pyscriptc_sqlite.execute(db, 'SELEKT 1')
    except RuntimeError as exc:
        print('bad_sql: RuntimeError: ' + str(exc))
    try:
        pyscriptc_sqlite.query(db, 'SELECT 1; SELECT 2')
    except RuntimeError as exc:
        print('two_statements: RuntimeError: ' + str(exc))
    pyscriptc_sqlite.close(db)


if __name__ == '__main__':
    main()
"""


@needs_clang
def test_sqlite_pilot_matches_the_stdlib_module(tmp_path: Path) -> None:
    """The fixture script's rows match what stdlib sqlite3 prints."""
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO t (name) VALUES ('ada'), ('bo')")
    expected = "".join(f"{row[1]}\n" for row in db.execute("SELECT id, name FROM t ORDER BY id"))
    db.close()

    binary = _build(tmp_path, DEMO)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected == "ada\nbo\n"


@needs_clang
def test_sqlite_pilot_surface_matches_the_stdlib_module(tmp_path: Path) -> None:
    """Bad SQL and two-statements-in-one raise like stdlib sqlite3 says.

    The adapter's contract (pyscriptc_sqlite.pyi): a refused statement is
    `RuntimeError` with "sqlite3: " plus SQLite's own text. The stdlib
    module raises `OperationalError`/`ProgrammingError` there, so the
    comparison is on the message text with the class mapping the adapter
    documents. Use after close is a compile-time PS4010 in the subset (the
    consumed handle is dead), so the stdlib leg covers the double close as
    the no-op both agree on, and the runtime leg stops at the first close.
    """
    import sqlite3

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    bad_sql = ""
    two_statements = ""
    for sql in ("SELEKT 1", "SELECT 1; SELECT 2"):
        try:
            db.execute(sql)
        except sqlite3.Error as exc:
            if sql == "SELEKT 1":
                bad_sql = str(exc)
            else:
                two_statements = str(exc)
    db.close()
    db.close()  # a no-op in stdlib sqlite3 and in the adapter alike

    expected = f"bad_sql: RuntimeError: sqlite3: {bad_sql}\ntwo_statements: RuntimeError: sqlite3: {two_statements}\n"

    binary = _build(tmp_path, SURFACE_DEMO)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == expected


@needs_clang
def test_use_after_close_is_refused_at_compile_time(tmp_path: Path) -> None:
    """The compiler refuses a statement on a closed connection: PS4010.

    stdlib sqlite3 raises ProgrammingError at runtime; the subset's
    ownership rule (F1b) moves the failure to the compile, which is the
    stricter contract the adapter documents.
    """
    source = (
        "import pyscriptc_sqlite\n"
        "\n"
        "def main() -> None:\n"
        "    db: pyscriptc_sqlite.Connection = pyscriptc_sqlite.connect(':memory:')\n"
        "    pyscriptc_sqlite.close(db)\n"
        "    pyscriptc_sqlite.execute(db, 'SELECT 1')\n"
    )
    module, _ = _lower(source)
    assert [fence["code"] for fence in module["fences"]] == ["PS4010"]


@needs_clang
def test_sqlite_pilot_is_leak_free_under_the_sanitizer(tmp_path: Path) -> None:
    """The connection, the rows and the strings free cleanly under ASan.

    The connection handle frees through the consuming `close` (F1b's
    scope-exit free when the script omits it); the returned
    `list[dict[str, str]]` is owned by the caller from the return on, and
    the runtime frees it on the normal path (F1c).
    """
    binary = _build(tmp_path, DEMO, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ada\nbo\n"
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr
    assert "runtime error" not in proc.stderr


@pytest.mark.linux_container
@pytest.mark.skipif(shutil.which("zig") is None, reason="zig not on PATH")
def test_sqlite_pilot_cross_compiles_for_musl(tmp_path: Path) -> None:
    """The same fixture script builds and runs on `x86_64-linux-musl`.

    This is the F3 gate's second target: the vendored sqlite3.c and the
    thunk compile with the target cc in the same per-unit loop as the
    runtime, and the binary links statically. The binary only executes
    where a Linux host or qemu-user runs it, so this lives in the
    `linux_container` suite (HEAVY=1 docker/run.sh).
    """
    binary = _build(tmp_path, DEMO, target="x86_64-linux-musl")
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ada\nbo\n"
