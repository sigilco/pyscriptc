"""Section GC: the compile and link flags that drop the unused sections.

The runtime and every vendored C unit land in one link, and most of what they
carry is code the program never calls. A linker that collects unused sections
drops it, so the flags are the difference between a 1.4 MB TLS client and a
370 KB one.

Two halves have to agree: the compile half gives every function and datum its
own section, the link half lets the linker drop the ones nothing references.
The link half is per object format, because the linkers spell it differently
(ELF `--gc-sections`, Mach-O `-dead_strip`, PE has no GNU-style spelling).

The tests here pin the flag shapes and the target-to-format mapping. The
measured effect (binary size, compile time) is recorded in the commit that
introduced the flags; the size test needs a real link and is marked as one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyscriptc.llvm import link_binary
from pyscriptc.sectiongc import (
    ELF,
    MACHO,
    PE,
    compile_flags,
    link_flags,
    object_format,
)


def _ll(tmp_path: Path) -> Path:
    ll = tmp_path / "m.ll"
    ll.write_text("define i32 @main() {\n  ret i32 0\n}\n", encoding="utf-8")
    return ll


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_NO_RT_CACHE", "1")


def test_the_host_target_is_elf_off_macos() -> None:
    assert object_format(None) == (MACHO if sys.platform == "darwin" else ELF)


def test_apple_triples_are_macho() -> None:
    for triple in ("aarch64-apple-ios", "aarch64-apple-ios-simulator", "x86_64-macos"):
        assert object_format(triple) == MACHO


def test_windows_triples_are_pe() -> None:
    for triple in ("x86_64-windows-gnu", "aarch64-windows-gnu"):
        assert object_format(triple) == PE


def test_posix_triples_are_elf() -> None:
    for triple in ("x86_64-linux-gnu", "aarch64-linux-gnu"):
        assert object_format(triple) == ELF


def test_wasm_triples_link_through_wasm_ld() -> None:
    for triple in ("wasm32-wasi-musl", "wasm32-wasi-p2"):
        assert object_format(triple) == "wasm"


def test_the_compile_flags_are_the_section_pair() -> None:
    assert compile_flags("x86_64-linux-gnu") == ["-ffunction-sections", "-fdata-sections"]
    assert compile_flags("aarch64-apple-ios") == ["-ffunction-sections", "-fdata-sections"]


def test_the_pe_lane_takes_no_compile_flags() -> None:
    """The PE link does not consume these sections, so it takes none."""
    assert compile_flags("x86_64-windows-gnu") == []


def test_the_link_flag_is_the_linker_spelling() -> None:
    assert link_flags("x86_64-linux-gnu") == ["-Wl,--gc-sections"]
    assert link_flags("wasm32-wasi-musl") == ["-Wl,--gc-sections"]
    assert link_flags("aarch64-apple-ios") == ["-Wl,-dead_strip"]


def test_the_pe_lane_takes_no_link_flag() -> None:
    assert link_flags("x86_64-windows-gnu") == []


def test_the_link_line_carries_the_gc_flag(tmp_path: Path) -> None:
    """The flag reaches the link line, not just the compile line."""
    record: list[list[str]] = []
    link_binary(_ll(tmp_path), tmp_path / "a", record=record)
    link = record[-1]
    assert "-Wl,--gc-sections" in link


def test_every_compile_carries_the_section_flags(tmp_path: Path) -> None:
    """The compile half is on every unit, so the link half has sections to collect."""
    record: list[list[str]] = []
    link_binary(_ll(tmp_path), tmp_path / "a", record=record)
    compiles = [cmd for cmd in record if "-c" in cmd]
    assert compiles
    for cmd in compiles:
        assert "-ffunction-sections" in cmd
        assert "-fdata-sections" in cmd


def test_the_mobile_library_flags_carry_the_section_pair() -> None:
    """The `--emit lib` objects carry the same compile half."""
    from pyscriptc.emitlib import library_flags
    from pyscriptc.mobile import mobile_target

    flags = library_flags(mobile_target("aarch64-apple-ios"))
    assert "-ffunction-sections" in flags
    assert "-fdata-sections" in flags
    assert "-fvisibility=hidden" in flags
