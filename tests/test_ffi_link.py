"""F2: the `--ffi` manifest units reach the link line (issue #220).

The static-link gate is the link step only: a manifest `[[source]]` entry
names a C file the target cc compiles, and a `[[source]] objects` entry names
an archive or object that goes straight on the link line. Both ride the same
link shape as the runtime, so a build with no manifest produces exactly the
link line it produced before the gate.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.errors import IRError
from pyscriptc.ffi_manifest import load_manifest
from pyscriptc.llvm import link_binary

ROOT = Path(__file__).resolve().parents[1]
FFIPROBE = ROOT / "packaging" / "zig" / "ffiprobe"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _ll(tmp_path: Path) -> Path:
    ll = tmp_path / "m.ll"
    ll.write_text("define i32 @main() {\n  ret i32 0\n}\n", encoding="utf-8")
    return ll


def _link(tmp_path: Path, **kwargs) -> list[list[str]]:
    """Link with the runtime object cache off, so the recorded shape is exact."""
    record: list[list[str]] = []
    link_binary(_ll(tmp_path), tmp_path / "a", record=record, **kwargs)
    return record


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    monkeypatch.setenv("PYSC_NO_RT_CACHE", "1")


# --- the no-manifest regression guard --------------------------------------


def test_a_build_with_no_manifest_has_the_master_link_line(tmp_path: Path) -> None:
    """The link line of a manifest-free build is the pre-gate shape.

    This is the regression guard for the F2 gate: the manifest units join the
    existing link shape, so a build that declares no manifest must produce a
    byte-identical link line.
    """
    record = _link(tmp_path)
    link = record[-1]
    assert link[0] == "clang"
    # every argument is a runtime object, then the fixed tail
    objects = link[1:-3]
    assert objects, "no objects on the link line"
    for obj in objects:
        assert obj.endswith(".o"), obj
    assert link[-3:] == ["-lm", "-o", str(tmp_path / "a")]
    # no manifest flags leak into a manifest-free build
    assert not any(arg.startswith("-l") and arg != "-lm" for arg in link)
    assert not any(arg.startswith("-L") for arg in link)


def test_a_build_with_no_manifest_compiles_the_runtime_units_only(tmp_path: Path) -> None:
    """The per-unit compile loop holds the runtime and nothing else."""
    record = _link(tmp_path)
    compiles = [cmd for cmd in record if "-c" in cmd]
    runtime = ROOT / "src" / "pyscriptc" / "runtime"
    for cmd in compiles:
        source = cmd[cmd.index("-c") + 1]
        assert source.startswith(str(runtime)) or source.endswith("m.ll"), source


# --- the manifest units ---------------------------------------------------


def test_a_manifest_source_compiles_in_the_per_unit_loop(tmp_path: Path) -> None:
    """A `[[source]] path` entry is compiled by the target cc, like a runtime unit."""
    unit = tmp_path / "vendor" / "demo.c"
    unit.parent.mkdir(parents=True)
    unit.write_text("int demo_add(int a) { return a + 1; }\n", encoding="utf-8")
    record = _link(tmp_path, compile_units=[unit])
    compiles = [cmd for cmd in record if "-c" in cmd]
    assert any(str(unit) in cmd for cmd in compiles), compiles
    # and it is not on the link line itself: the linker only sees the object
    assert not any(str(unit) == arg for arg in record[-1])


def test_a_manifest_object_goes_straight_on_the_link_line(tmp_path: Path) -> None:
    """A `[[source]] objects` entry is a prebuilt unit: it is not compiled."""
    archive = tmp_path / "vendor" / "libdemo.a"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"!<arch>\n")
    record = _link(tmp_path, link_units=[archive])
    assert str(archive) in record[-1]
    compiles = [cmd for cmd in record if "-c" in cmd]
    assert not any(str(archive) in cmd for cmd in compiles), compiles


def test_manifest_flags_reach_the_link_line(tmp_path: Path) -> None:
    """The per-manifest `-L` and `-l` flags land on the link line."""
    record = _link(tmp_path, link_flags=["-L/opt/ffi/lib", "-lz"])
    link = record[-1]
    assert "-L/opt/ffi/lib" in link
    assert "-lz" in link
    # the flags come before the fixed tail, so the linker resolves them
    assert link.index("-lz") < link.index("-lm")


def test_a_missing_unit_is_refused_before_the_linker_runs(tmp_path: Path) -> None:
    """A declared unit that is not on disk fails before any cc runs."""
    missing = tmp_path / "vendor" / "gone.c"
    with pytest.raises(IRError, match="does not exist"):
        _link(tmp_path, compile_units=[missing])


def test_a_missing_object_is_refused_before_the_linker_runs(tmp_path: Path) -> None:
    missing = tmp_path / "vendor" / "gone.a"
    with pytest.raises(IRError, match="does not exist"):
        _link(tmp_path, link_units=[missing])


# --- the manifest schema --------------------------------------------------


def test_the_manifest_splits_its_units_into_compile_and_link(tmp_path: Path) -> None:
    manifest = tmp_path / "ffi.toml"
    manifest.write_text(
        '[manifest]\nmodule = "demo"\nps_abi = 1\nformat = 1\n'
        '[[source]]\npath = "vendor/demo.c"\n'
        '[[source]]\nobjects = ["vendor/pre.a"]\n',
        encoding="utf-8",
    )
    parsed = load_manifest(manifest)
    assert parsed.compile_units() == [tmp_path / "vendor" / "demo.c"]
    assert parsed.link_units() == [tmp_path / "vendor" / "pre.a"]
    # the flat list keeps both, in declaration order
    assert parsed.unit_paths() == [
        tmp_path / "vendor" / "demo.c",
        tmp_path / "vendor" / "pre.a",
    ]


def test_a_manifest_without_sources_declares_no_units(tmp_path: Path) -> None:
    manifest = tmp_path / "ffi.toml"
    manifest.write_text('[manifest]\nmodule = "demo"\nps_abi = 1\nformat = 1\n', encoding="utf-8")
    parsed = load_manifest(manifest)
    assert parsed.compile_units() == []
    assert parsed.link_units() == []
    assert parsed.link_flags() == []


def test_manifest_libs_become_link_flags(tmp_path: Path) -> None:
    manifest = tmp_path / "ffi.toml"
    manifest.write_text(
        '[manifest]\nmodule = "demo"\nps_abi = 1\nformat = 1\nlibs = ["z"]\nlib_dirs = ["/opt/ffi/lib"]\n',
        encoding="utf-8",
    )
    parsed = load_manifest(manifest)
    assert parsed.link_flags() == ["-L/opt/ffi/lib", "-lz"]


# --- the end-to-end compile ------------------------------------------------


@needs_clang
def test_a_manifest_source_is_linked_into_the_binary(tmp_path: Path) -> None:
    """A manifest with a C source compiles and runs, with the C unit linked in."""
    source = tmp_path / "use.py"
    source.write_text(
        "import ffiprobe\n\ndef main() -> int:\n    return ffiprobe.add3(1, 2, 3)\n",
        encoding="utf-8",
    )
    binary = tmp_path / "use"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--ffi",
            str(FFIPROBE / "ffi.toml"),
            "--skip-check",
            "--run",
            "--call",
            "main",
            "--args",
            "[]",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "6\n"


@needs_clang
def test_a_manifest_source_includes_ps_rt_h(tmp_path: Path) -> None:
    """A thunk written against the runtime contract compiles: ps_rt.h resolves.

    The sqlite adapter's thunk does exactly this (`#include "ps_rt.h"`), so
    the manifest units get the runtime directory on their include path; the
    vendored headers next to the manifest resolve too.
    """
    unit = tmp_path / "vendor" / "thunk.c"
    unit.parent.mkdir(parents=True)
    unit.write_text(
        '#include "ps_rt.h"\n#include "vendor/vendored.h"\nint demo(void) { return DEMO + 1; }\n',
        encoding="utf-8",
    )
    (tmp_path / "vendor" / "vendored.h").write_text("#define DEMO 41\n", encoding="utf-8")
    source = tmp_path / "use.py"
    source.write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    manifest = tmp_path / "ffi.toml"
    manifest.write_text(
        '[manifest]\nmodule = "demo"\nps_abi = 1\nformat = 1\n[[source]]\npath = "vendor/thunk.c"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(source),
            "--ffi",
            str(manifest),
            "--emit",
            "bin",
            "--skip-check",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_a_manifest_with_a_missing_source_is_refused(tmp_path: Path) -> None:
    """The CLI refuses a manifest whose unit is not on disk, before the build."""
    source = tmp_path / "use.py"
    source.write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    manifest = tmp_path / "ffi.toml"
    manifest.write_text(
        '[manifest]\nmodule = "demo"\nps_abi = 1\nformat = 1\n[[source]]\npath = "vendor/gone.c"\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(source), "--ffi", str(manifest), "--skip-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "PS4009" in proc.stderr
    assert "gone.c" in proc.stderr
