"""Section garbage collection: the compile and link flags one object format takes.

Every runtime object and every vendored C unit lands in one link, and most of
what they carry is code the program never calls: the mbedTLS server side, the
DTLS stacks, the runtime surfaces a script never touches. A linker that
collects unused sections drops them from the binary, so the flags below are
the difference between a megabyte of TLS client and a few hundred kilobytes.

The two halves have to agree: `-ffunction-sections`/`-fdata-sections` at
compile time put every function and every datum in its own section, and the
link-time flag lets the linker drop the ones nothing references. A linker
that never sees the sections cannot collect them.

The link flag is per object format, because the three linkers spell it
differently:

- ELF (Linux, Android, wasm-ld): `-Wl,--gc-sections`.
- Mach-O (macOS, iOS): `-Wl,-dead_strip`.
- PE (Windows): the MSVC linker spells it `/OPT:REF`, which the GNU-style
  driver does not take, so the Windows lane collects nothing and the flags
  stay off.

A target the compiler does not know (a triple with no recognizable family)
is treated as ELF, the format every POSIX target uses.
"""

from __future__ import annotations

import sys

# The object formats a target's link can produce.
ELF = "elf"
MACHO = "macho"
PE = "pe"
WASM = "wasm"


def object_format(target: str | None) -> str:
    """The object format one target's link produces.

    `target` is the zig-style triple the compile carries (`None` is the host
    build). The host decides from the platform: macOS links Mach-O, every
    other POSIX host links ELF.
    """
    if target is None:
        return MACHO if sys.platform == "darwin" else ELF
    triple = target.lower()
    if "windows" in triple or "mingw" in triple:
        return PE
    if "wasm" in triple:
        return WASM
    if any(family in triple for family in ("darwin", "macos", "apple", "ios")):
        return MACHO
    return ELF


def compile_flags(target: str | None) -> list[str]:
    """The compile flags that give the linker sections to collect.

    Every function and every datum goes into its own section, so the
    link-time collector can drop the ones nothing references. The PE lane
    gets nothing: the MSVC-shaped link does not consume these sections, and
    the flags would only change the object without a matching collector.
    """
    if object_format(target) == PE:
        return []
    return ["-ffunction-sections", "-fdata-sections"]


def link_flags(target: str | None) -> list[str]:
    """The link flag that collects the sections nothing references.

    The flag is the linker spell, not the compiler spell: `-Wl,--gc-sections`
    for ELF and wasm-ld, `-Wl,-dead_strip` for Mach-O. The PE lane has no
    flag here, so the Windows link keeps every section.
    """
    fmt = object_format(target)
    if fmt == ELF or fmt == WASM:
        return ["-Wl,--gc-sections"]
    if fmt == MACHO:
        return ["-Wl,-dead_strip"]
    return []
