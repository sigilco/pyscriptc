"""F-series: the pyscriptc-arrow end-to-end pilot (issue #1209).

The full pipeline, not the host-thunk differential (that is
`test_ffi_arrow_adapter.py`): a fixture script with `import
pyscriptc_arrow` lowers through `lower_file` with the adapter manifest
installed, emits, and links the vendored nanoarrow bundle plus the thunk
through the `[[source]]` seam of F2. The assertions are the acceptance
criteria:

- a compiled script builds an Arrow array, reads it back element by
  element and exchanges it over the Arrow IPC stream wire form; the
  exported bytes open in PyArrow and carry the same values;
- the same source cross-compiles for `x86_64-linux-musl` (compile gate);
- the raise surface (an unknown dtype word, use after release) comes back
  as the ValueError the adapter documents.

The vendored bundle is fetched by `fetch_vendor.py` and hash-pinned;
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

pa = pytest.importorskip("pyarrow", reason="PyArrow (dev extra) is not installed")

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "packaging" / "adapters" / "pyscriptc-arrow"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _vendor() -> Path:
    """The fetched bundle, or a skip when the pin cannot be fetched."""
    spec = importlib.util.spec_from_file_location("arrow_fetch_vendor", ADAPTER / "fetch_vendor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.fetch()
    except OSError as exc:  # offline: the pin can not be fetched
        pytest.skip(f"cannot fetch the pinned nanoarrow bundle: {exc}")


def _manifest():
    """The adapter manifest, with the vendored bundle in place."""
    _vendor()
    return load_manifest(ADAPTER / "ffi.toml")


def _lower(source: str):
    manifest = _manifest()
    with ffi_modules([manifest]):
        return lower_source(source, "arrow_demo.py"), manifest


def _build(tmp_path: Path, source: str, *, sanitize: list[str] | None = None, target: str | None = None) -> Path:
    """Lower, emit and link one arrow program through the adapter manifest.

    The units are the manifest's own `[[source]]` entries (the vendored
    nanoarrow bundle and the thunk), so the link path is the same one a
    user compiles with `--ffi`, not a hand-assembled unit list.
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
        include_dirs=manifest.include_dir_list(),
        sanitize=sanitize,
        target=target,
        driver="zig" if target is not None else "clang",
    )
    return binary


DEMO = """import pyscriptc_arrow


def main() -> None:
    values: list[int] = [11, 22, 33]
    array: pyscriptc_arrow.Array = pyscriptc_arrow.import_array(pyscriptc_arrow.INT64, values)
    print(pyscriptc_arrow.array_length(array))
    print(pyscriptc_arrow.array_get(array, 1))
    print(pyscriptc_arrow.array_sum(array))
    wire: bytes = pyscriptc_arrow.export_array(array)
    pyscriptc_arrow.release(array)
    stream: pyscriptc_arrow.Stream = pyscriptc_arrow.import_stream(wire)
    rows: list[dict[str, str]] = pyscriptc_arrow.stream_read(stream)
    for row in rows:
        print(row['v'])
    pyscriptc_arrow.stream_release(stream)


if __name__ == '__main__':
    main()
"""


@needs_clang
def test_arrow_pilot_matches_pyarrow(tmp_path: Path) -> None:
    """The fixture script's values match what PyArrow prints."""
    binary = _build(tmp_path, DEMO)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "3\n22.0\n66.0\n11\n22\n33\n"


@needs_clang
def test_arrow_pilot_wire_opens_in_pyarrow(tmp_path: Path) -> None:
    """The compiled program exports; PyArrow reads the same bytes."""
    exchange = """import pyscriptc_arrow


def main() -> None:
    values: list[int] = [4, 5, 6, 7]
    array: pyscriptc_arrow.Array = pyscriptc_arrow.import_array(pyscriptc_arrow.INT64, values)
    wire: bytes = pyscriptc_arrow.export_array(array)
    pyscriptc_arrow.release(array)
    n: int = len(wire)
    print(n)
    for i in range(n):
        print(wire[i])


if __name__ == '__main__':
    main()
"""
    binary = _build(tmp_path, exchange)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    n = int(lines[0])
    wire = bytes(int(b) for b in lines[1 : 1 + n])
    with pa.ipc.open_stream(pa.BufferReader(wire)) as reader:
        batch = reader.read_next_batch()
    assert batch.schema.names == ["v"]
    assert batch.column(0).to_pylist() == [4, 5, 6, 7]


@needs_clang
def test_arrow_pilot_cross_compiles_for_musl(tmp_path: Path) -> None:
    """The same source links for x86_64-linux-musl (compile gate)."""
    if shutil.which("zig") is None:
        pytest.skip("no zig driver")
    binary = _build(tmp_path, DEMO, target="x86_64-linux-musl")
    assert binary.is_file() and binary.stat().st_size > 0


SURFACE_DEMO = """import pyscriptc_arrow


def main() -> None:
    values: list[int] = [1]
    array: pyscriptc_arrow.Array = pyscriptc_arrow.import_array(99, values)
    print(pyscriptc_arrow.array_length(array))


if __name__ == '__main__':
    main()
"""


@needs_clang
def test_unknown_dtype_word_raises_like_the_pyi_documents(tmp_path: Path) -> None:
    """The thunk refuses a dtype word outside the subset with ValueError."""
    binary = _build(tmp_path, SURFACE_DEMO)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode != 0
    assert "arrow: the dtype is not one of INT32, INT64, FLOAT32, FLOAT64, BOOL or UTF8." in proc.stderr


USE_AFTER_RELEASE = """import pyscriptc_arrow


def main() -> None:
    values: list[int] = [1]
    array: pyscriptc_arrow.Array = pyscriptc_arrow.import_array(pyscriptc_arrow.INT64, values)
    pyscriptc_arrow.release(array)
    print(pyscriptc_arrow.array_length(array))


if __name__ == '__main__':
    main()
"""


@needs_clang
def test_use_after_release_is_refused_at_compile_time(tmp_path: Path) -> None:
    """The compiler refuses a read on a released array: PS4010."""
    module, _ = _lower(USE_AFTER_RELEASE)
    assert [fence["code"] for fence in module["fences"]] == ["PS4010"]
