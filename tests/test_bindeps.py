# SPDX-License-Identifier: Apache-2.0
"""The post-link host-libpython audit (issue #1247).

`bindeps.host_libpython_references` parses `otool -L` / `readelf -d` output
purely; `bindeps.audit_libpython` picks the tool by platform with a `tool`
seam for tests. The end-to-end seam is the CLI: a host island link exits
non-zero (or warns under an explicit `--embed-python=host`) with the dylib
path in the message, and a native-only binary prints nothing and exits 0.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from pyscriptc import bindeps

HOST_DYLIB = "/Users/builds/uv/python/cpython-3.14.9-macos-aarch64-none/lib/libpython3.14.dylib"

MACHO_HOST = f"""prog:
\t{HOST_DYLIB} (compatibility version 3.14.0, current version 3.14.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.40.3)
\t/usr/lib/libcurl.4.dylib (compatibility version 7.0.0, current version 9.0.0)
"""

MACHO_BUNDLE = """prog:
\t@executable_path/../lib/libpython3.14.dylib (compatibility version 3.14.0, current version 3.14.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.40.3)
"""

MACHO_RPATH_BUNDLE = """prog:
\t@rpath/libpython3.14.dylib (compatibility version 3.14.0, current version 3.14.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.40.3)
"""

MACHO_RPATH_BUNDLE_LOAD = """Load command 12
          cmd LC_RPATH
      cmdsize 40
         path @executable_path/../lib (offset 12)
Load command 13
          cmd LC_RPATH
      cmdsize 32
         path @loader_path/../lib (offset 12)
"""

MACHO_RPATH_HOST_LOAD = """Load command 12
          cmd LC_RPATH
      cmdsize 96
         path /Users/builds/uv/python/cpython-3.14.9-macos-aarch64-none/lib (offset 12)
"""

ELF_HOST = """
Dynamic section at offset 0x2d80 contains 29 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libpython3.14.so.1.0]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000001d (RUNPATH)            Library runpath: [/opt/py/lib]
"""

ELF_HOST_NO_RUNPATH = """
Dynamic section at offset 0x2d80 contains 28 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libpython3.14.so.1.0]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
"""

ELF_BUNDLE = """
Dynamic section at offset 0x2d80 contains 29 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libpython3.14.so]
 0x0000000000000001 (NEEDED)             Shared library: [libcurl.so.4]
 0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN/../lib]
"""


def test_macho_host_dylib_is_flagged() -> None:
    refs = bindeps.host_libpython_references(MACHO_HOST)
    assert refs == [HOST_DYLIB]


def test_macho_relative_rpath_dylib_is_portable() -> None:
    assert bindeps.host_libpython_references(MACHO_BUNDLE) == []


def test_macho_rpath_soname_with_only_relative_rpaths_is_portable() -> None:
    """A `@rpath/...` soname the loader resolves through relative rpaths
    travels with the binary."""
    assert bindeps.host_libpython_references(MACHO_RPATH_BUNDLE, MACHO_RPATH_BUNDLE_LOAD) == []


def test_macho_rpath_soname_with_an_absolute_rpath_is_the_host() -> None:
    """A `@rpath/...` soname with an absolute rpath resolves to the build
    host's library: the loader searches the rpaths in order."""
    assert bindeps.host_libpython_references(MACHO_RPATH_BUNDLE, MACHO_RPATH_HOST_LOAD) == [
        "@rpath/libpython3.14.dylib"
    ]


def test_macho_rpath_soname_without_a_load_reading_is_the_host() -> None:
    """With no rpath reading at all the reference stays flagged: the audit
    does not assume a relative rpath it cannot see."""
    assert bindeps.host_libpython_references(MACHO_RPATH_BUNDLE) == ["@rpath/libpython3.14.dylib"]


def test_macho_no_libpython_is_quiet() -> None:
    assert bindeps.host_libpython_references("prog:\n\t/usr/lib/libSystem.B.dylib (x)\n") == []


def test_elf_absolute_needed_is_flagged() -> None:
    assert bindeps.host_libpython_references(ELF_HOST) == ["libpython3.14.so.1.0"]


def test_elf_no_runpath_is_the_host_search_path() -> None:
    assert bindeps.host_libpython_references(ELF_HOST_NO_RUNPATH) == ["libpython3.14.so.1.0"]


def test_elf_relative_rpath_needed_is_portable() -> None:
    # The bundle's NEEDED entry is the unversioned soname resolved through
    # $ORIGIN/../lib; the library travels with the bundle.
    assert bindeps.host_libpython_references(ELF_BUNDLE) == []


def test_audit_uses_the_tool_seam(tmp_path: Path) -> None:
    seen: list[Path] = []

    def tool(binary: Path) -> str:
        seen.append(binary)
        return MACHO_HOST

    refs = bindeps.audit_libpython(tmp_path / "prog", tool=tool)
    assert seen == [tmp_path / "prog"]
    assert len(refs) == 1 and "libpython" in refs[0]


def test_audit_without_a_tool_degrades_to_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bindeps.shutil, "which", lambda _name: None)
    monkeypatch.setattr(bindeps.sys, "platform", "linux")
    assert bindeps.audit_libpython(tmp_path / "prog") == []


def _clang_available() -> bool:
    import shutil

    return shutil.which("clang") is not None


needs_clang = pytest.mark.skipif(not _clang_available(), reason="needs a C toolchain")


def _host_has_shared_libpython() -> bool:
    """True when this interpreter has a shared libpython to link against.

    A release binary and a python-build-standalone install have none, so
    `auto` resolves to `bundle` there and the host-link tests have nothing
    to link: they skip instead of asserting a link that never happens.
    """
    from pyscriptc import embed

    return embed.host_has_shared_libpython()


needs_host_libpython = pytest.mark.skipif(
    not _host_has_shared_libpython(), reason="this interpreter has no shared libpython to link"
)

ISLAND_SRC = """import fractions

def main() -> None:
    f = fractions.Fraction(3, 4)
    s: str = str(f + fractions.Fraction(1, 6))
    print(s)

if __name__ == "__main__":
    main()
"""


def _compile(src: Path, cwd: Path, extra: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(src), "--emit", "bin", "-o", "prog", *extra],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )


@needs_clang
def test_native_binary_prints_nothing_and_exits_zero(tmp_path: Path) -> None:
    """A native-only compile never pays the audit: exit 0, no stderr line."""
    src = tmp_path / "prog.py"
    src.write_text("def main() -> None:\n    print(1 + 2)\n\nmain()\n", encoding="utf-8")
    done = _compile(src, tmp_path, [])
    assert done.returncode == 0, done.stderr
    assert "libpython" not in done.stderr


@needs_clang
@needs_host_libpython
def test_host_island_link_fails_with_the_dylib_path(tmp_path: Path) -> None:
    """The host island link refuses: non-zero, the dylib path and the remedy
    in the message."""
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    done = _compile(src, tmp_path, ["--island", "--embed-python=host"])
    assert done.returncode == 1, done.stderr
    assert "libpython" in done.stderr
    assert sysconfig.get_config_var("VERSION") in done.stderr
    assert "--embed-python=bundle" in done.stderr


@needs_clang
@needs_host_libpython
def test_allow_host_libpython_warns_but_links(tmp_path: Path) -> None:
    """`--allow-host-libpython` keeps the deliberate host link a way through:
    a named warning with the dylib path, the compile still succeeds."""
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    (tmp_path / "pyscriptc.toml").write_text('[imports]\nfractions = "island"\n', encoding="utf-8")
    done = _compile(src, tmp_path, ["--island", "--embed-python=host", "--allow-host-libpython"])
    assert done.returncode == 0, done.stderr
    assert "warning" in done.stderr
    assert "libpython" in done.stderr
    assert (tmp_path / "prog").is_file()


@needs_clang
def test_a_bundle_link_carries_no_host_load_command(tmp_path: Path) -> None:
    """A bundle links the pack's library only: no absolute host path.

    The bundle's own link line used to add the host's `-L`/`-l`/`-rpath` on
    top of the pack's relative rpath, and the absolute load command won at
    load time: the bundle ran only where the build machine's Python lived.
    The audit is what found it (issue #1247).
    """
    src = tmp_path / "prog.py"
    src.write_text(ISLAND_SRC, encoding="utf-8")
    done = _compile(src, tmp_path, ["--island", "--embed-python=bundle", "-o", "out"])
    assert done.returncode == 0, done.stderr
    binary = tmp_path / "out" / "bin" / "out"
    assert binary.is_file()
    if sys.platform == "darwin":
        listed = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, timeout=60)
        load = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, timeout=60)
        assert listed.returncode == 0 and load.returncode == 0
        assert "@executable_path/../lib" in load.stdout
        assert "@rpath/libpython" in listed.stdout
        # The build machine's Python path never appears in the image.
        assert str(Path(sys.executable).resolve().parent.parent) not in listed.stdout
        assert str(Path(sys.executable).resolve().parent.parent) not in load.stdout
    else:
        listed = subprocess.run(["readelf", "-d", str(binary)], capture_output=True, text=True, timeout=60)
        assert listed.returncode == 0
        assert "$ORIGIN/../lib" in listed.stdout
        assert str(Path(sys.executable).resolve().parent.parent) not in listed.stdout
