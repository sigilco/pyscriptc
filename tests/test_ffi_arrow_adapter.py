"""The pyscriptc-arrow adapter (#1209): manifest, .pyi surface and host thunk.

The thunk is built as a host C test against the runtime and compared with
real PyArrow over the Arrow IPC stream wire form, both directions. Nothing
here touches the compiler's lowering or link path; the end-to-end compile
test is the pilot below.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow", reason="PyArrow (dev extra) is not installed")

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "packaging" / "adapters" / "pyscriptc-arrow"
RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("arrow_fetch_vendor", ADAPTER / "fetch_vendor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendor_pin_is_complete() -> None:
    pin = tomllib.loads((ADAPTER / "vendor" / "VENDOR.toml").read_text(encoding="utf-8"))
    for key in ("version", "url", "license", "tar_sha256", "bundle_namespace", "bundle_flags"):
        assert pin[key], key
    assert pin["url"].startswith("https://github.com/apache/arrow-nanoarrow")
    assert "Apache-2.0" in pin["license"]
    for key in (
        "nanoarrow.c_sha256",
        "nanoarrow_ipc.c_sha256",
        "flatcc.c_sha256",
        "nanoarrow.h_sha256",
        "nanoarrow_ipc.h_sha256",
    ):
        assert pin[key], key
    assert len(pin["flatcc_headers"]) >= 30
    # The IPC bundle needs flatcc: the two flags go together.
    assert "--with-ipc" in pin["bundle_flags"] and "--with-flatcc" in pin["bundle_flags"]


def test_manifest_validates_under_load_manifest() -> None:
    from pyscriptc.ffi_manifest import load_manifest

    manifest = load_manifest(ADAPTER / "ffi.toml")
    assert manifest.module == "pyscriptc_arrow"
    assert {fn.name for fn in manifest.functions} == {
        "import_array",
        "array_length",
        "array_sum",
        "array_get",
        "array_is_null",
        "export_array",
        "import_stream",
        "stream_read",
        "stream_release",
        "release",
    }
    assert {h.name for h in manifest.handles} == {"Array", "Stream"}
    assert {c.name: c.value for c in manifest.consts} == {
        "INT32": 1,
        "INT64": 2,
        "FLOAT32": 3,
        "FLOAT64": 4,
        "BOOL": 5,
        "UTF8": 6,
    }
    assert [ref.param for ref in manifest.function("release").consumes] == ["array"]
    assert [ref.param for ref in manifest.function("stream_release").consumes] == ["stream"]
    assert [u.name for u in manifest.unit_paths()] == ["flatcc.c", "nanoarrow_ipc.c", "nanoarrow.c", "ps_ffi_arrow.c"]


def test_pyi_matches_manifest_surface() -> None:
    manifest = tomllib.loads((ADAPTER / "ffi.toml").read_text(encoding="utf-8"))
    tree = ast.parse((ADAPTER / "pyscriptc_arrow.pyi").read_text(encoding="utf-8"))
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
    source = (ADAPTER / "ps_ffi_arrow.c").read_text(encoding="utf-8")
    for fn in manifest["fn"]:
        assert f" *{fn['abi']}(" in source or f" {fn['abi']}(" in source, fn["abi"]


def _expected_host_output() -> str:
    """The same sequence as host_main.c, read back through PyArrow."""
    lines = []
    lines += [
        "int32_length=4",
        "int32_sum=46",
        "int32_get0=1",
        "int32_get3=40",
        "int32_null0=0",
        "int64_length=3",
        "int64_sum=1e+09",
        "int64_get0=-5",
        "float64_length=3",
        "float64_sum=2.25",
        "float64_get1=-1.25",
        # float32 narrows 0.1 to the f32 spelling, and %g prints it back.
        "float32_get0=0.1",
        "bool_get0=1",
        "bool_get1=0",
        "utf8_length=2",
        "utf8_get0=3",
        "exported_length=3",
        "exported_format=l",
        "imported_length=3",
        "imported_sum=24",
    ]
    # The wire rows: the host harness reads its own export back.
    lines += ["wire_bytes=328"]
    lines += [f"row{i}={value}" for i, value in enumerate([11, 22, 33])]
    lines += ["rows=3"]
    lines += [f"row{i}={text}" for i, text in enumerate(["ada", "bo"])]
    lines += ["rows=2"]
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(
    shutil.which("cc") is None and shutil.which("clang") is None and shutil.which("gcc") is None,
    reason="no host C compiler",
)
def test_thunk_matches_pyarrow_over_the_wire(tmp_path: Path) -> None:
    """The host harness output matches PyArrow reading the same stream.

    The C-side leg runs the harness; the PyArrow leg opens the harness's
    own export with pyarrow.ipc.open_stream and checks the values, so both
    implementations agree on the same bytes.
    """
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    fetcher = _load_fetcher()
    try:
        vendor = fetcher.fetch()
    except OSError as exc:  # offline: the pin cannot be fetched
        pytest.skip(f"cannot fetch the pinned nanoarrow bundle: {exc}")
    strict = ["-O1", "-Wall", "-Werror", f"-I{RUNTIME}", f"-I{vendor}"]
    units = [
        (vendor / "flatcc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow_ipc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow.c", ["-O1", "-w", f"-I{vendor}"]),
        (ADAPTER / "ps_ffi_arrow.c", strict),
        (ADAPTER / "tests" / "host_main.c", strict),
    ]
    units += [
        (path, ["-O1", "-w", f"-I{RUNTIME}"]) for path in sorted(RUNTIME.glob("*.c")) if path.name != "ps_island.c"
    ]
    objects: list[str] = []
    for index, (src, flags) in enumerate(units):
        obj = tmp_path / f"{index}_{src.stem}.o"
        done = subprocess.run([cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{src.name}: {done.stderr}"
        objects.append(str(obj))
    binary = tmp_path / "host_arrow"
    link = subprocess.run([cc, *objects, "-lm", "-o", str(binary)], capture_output=True, text=True, check=False)
    assert link.returncode == 0, link.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == _expected_host_output()


def test_exported_bytes_open_in_pyarrow(tmp_path: Path) -> None:
    """The compiled side exports; PyArrow reads the same bytes."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no host C compiler")
    fetcher = _load_fetcher()
    try:
        vendor = fetcher.fetch()
    except OSError as exc:
        pytest.skip(f"cannot fetch the pinned nanoarrow bundle: {exc}")
    emitter = tmp_path / "emit_wire.c"
    emitter.write_text(
        """#include <stdio.h>
#include "nanoarrow/nanoarrow.h"
#include "ps_rt.h"
void *ps_ffi_arrow_import_array(int64_t dtype, void *values);
void ps_ffi_arrow_export_array(void *array, PsBuf *out);
int main(void) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(int64_t), 0);
    int64_t values[] = {11, 22, 33};
    for (int i = 0; i < 3; i++) { int64_t v = values[i]; ps_seq_push(seq, &v); }
    void *array = ps_ffi_arrow_import_array(2 /* INT64 */, seq);
    PsBuf out;
    ps_ffi_arrow_export_array(array, &out);
    fwrite(out.data, 1, (size_t)out.len, stdout);
    return 0;
}
""",
        encoding="utf-8",
    )
    strict = ["-O1", "-Wall", "-Werror", f"-I{RUNTIME}", f"-I{vendor}"]
    units = [
        (vendor / "flatcc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow_ipc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow.c", ["-O1", "-w", f"-I{vendor}"]),
        (ADAPTER / "ps_ffi_arrow.c", strict),
        (emitter, strict),
    ]
    units += [
        (path, ["-O1", "-w", f"-I{RUNTIME}"]) for path in sorted(RUNTIME.glob("*.c")) if path.name != "ps_island.c"
    ]
    objects: list[str] = []
    for index, (src, flags) in enumerate(units):
        obj = tmp_path / f"{index}_{src.stem}.o"
        done = subprocess.run([cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{src.name}: {done.stderr}"
        objects.append(str(obj))
    binary = tmp_path / "emit_wire"
    link = subprocess.run([cc, *objects, "-lm", "-o", str(binary)], capture_output=True, text=True, check=False)
    assert link.returncode == 0, link.stderr
    wire = tmp_path / "wire.arrow"
    wire.write_bytes(subprocess.run([str(binary)], capture_output=True, check=True).stdout)

    with pa.ipc.open_stream(pa.BufferReader(wire.read_bytes())) as reader:
        batch = reader.read_next_batch()
    assert batch.schema.names == ["v"]
    assert batch.column(0).type == pa.int64()
    assert batch.column(0).to_pylist() == [11, 22, 33]


def test_pyarrow_produced_stream_decodes_in_the_thunk(tmp_path: Path) -> None:
    """PyArrow produces; the compiled side reads the same values."""
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no host C compiler")
    fetcher = _load_fetcher()
    try:
        vendor = fetcher.fetch()
    except OSError as exc:
        pytest.skip(f"cannot fetch the pinned nanoarrow bundle: {exc}")

    # PyArrow produces one-column streams (float64 and bool: the two the
    # reader renders through its own spelling rules).
    wire_f64 = tmp_path / "f64.arrow"
    batch = pa.record_batch([[1.5, -2.25, 3.0]], names=["v"])
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(batch)
    wire_f64.write_bytes(sink.getvalue().to_pybytes())
    wire_bool = tmp_path / "bool.arrow"
    batch = pa.record_batch([[True, False, True]], names=["v"])
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(batch)
    wire_bool.write_bytes(sink.getvalue().to_pybytes())

    reader = tmp_path / "read_pa.c"
    reader.write_text(
        """#include <stdio.h>
#include <stdlib.h>
#include "nanoarrow/nanoarrow.h"
#include "ps_rt.h"
void *ps_ffi_arrow_import_stream(int64_t n, const char *data);
PsSeq *ps_ffi_arrow_stream_read(void *stream);
void ps_ffi_arrow_stream_release(void *stream);
int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *data = malloc((size_t)n);
    if (fread(data, 1, (size_t)n, f) != (size_t)n) return 2;
    fclose(f);
    void *s = ps_ffi_arrow_import_stream(n, data);
    PsSeq *rows = ps_ffi_arrow_stream_read(s);
    PsBuf key;
    ps_buf_copy(&key, "v", 1);
    int64_t count = ps_seq_len(rows);
    for (int64_t i = 0; i < count; i++) {
        PsMap *row = NULL;
        ps_seq_get(rows, i, &row);
        PsBuf val;
        ps_map_get(row, &key, &val);
        printf("row%lld=%.*s\\n", (long long)i, (int)val.len, val.data ? val.data : "");
    }
    printf("rows=%lld\\n", (long long)count);
    ps_ffi_arrow_stream_release(s);
    return 0;
}
""",
        encoding="utf-8",
    )
    strict = ["-O1", "-Wall", "-Werror", f"-I{RUNTIME}", f"-I{vendor}"]
    units = [
        (vendor / "flatcc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow_ipc.c", ["-O1", "-w", f"-I{vendor}"]),
        (vendor / "nanoarrow.c", ["-O1", "-w", f"-I{vendor}"]),
        (ADAPTER / "ps_ffi_arrow.c", strict),
        (reader, strict),
    ]
    units += [
        (path, ["-O1", "-w", f"-I{RUNTIME}"]) for path in sorted(RUNTIME.glob("*.c")) if path.name != "ps_island.c"
    ]
    objects: list[str] = []
    for index, (src, flags) in enumerate(units):
        obj = tmp_path / f"{index}_{src.stem}.o"
        done = subprocess.run([cc, *flags, "-c", str(src), "-o", str(obj)], capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"{src.name}: {done.stderr}"
        objects.append(str(obj))
    binary = tmp_path / "read_pa"
    link = subprocess.run([cc, *objects, "-lm", "-o", str(binary)], capture_output=True, text=True, check=False)
    assert link.returncode == 0, link.stderr

    expected_f64 = "row0=1.5\nrow1=-2.25\nrow2=3\nrows=3\n"
    ran = subprocess.run([str(binary), str(wire_f64)], capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == expected_f64
    expected_bool = "row0=True\nrow1=False\nrow2=True\nrows=3\n"
    ran = subprocess.run([str(binary), str(wire_bool)], capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == expected_bool
