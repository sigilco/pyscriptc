# SPDX-License-Identifier: Apache-2.0
"""Post-link audit of a produced binary's dynamic dependencies (issue #1247).

Everything here is boxed-only code (the audit exists for the island link; the
ledger counts this module whole, see `.agents/docs/boxed-cost.md`).

A compile can link whatever the embed mode resolves: `--embed-python=host`
(and `auto` on an interpreter with a shared libpython) links the build host's
libpython by path. That binary runs on the build machine and breaks on a
machine without that Python, and until this audit ran the only way to know was
`otool -L` / `readelf -d` by hand. `audit_libpython` reads the linked artifact
the same way and hands back the host-libpython reference, so the CLI can fail
loudly with the dylib path and the remedy while `--embed-python=host` and
`auto` keep a way through.

"Host" is decided structurally, not by matching the link line's `-L`:
a `libpython` reference reached through the bundle's relative rpath
(`@executable_path/../lib`, `$ORIGIN/../lib`) or the system library search
paths (`/usr/lib`, `lib/`, `/lib/...`) travels with or is provided by the
target, so it is not flagged. An ELF loader resolves a bare `NEEDED` soname
through the binary's own `RUNPATH`, so an ELF `libpython` reference is the
host's exactly when the binary's runpath is absolute (or empty); the parsers
read that runpath from the same `readelf -d` output. An absolute reference
into the Python installation (`/opt/.../lib/libpython3.14.dylib`,
`/usr/local/...`) is the non-portable host link. The PyPy engine packs are
excluded: `libpypy*-c` is not `libpython`, and its link flags pin a relative
rpath by construction.

The readers are `otool -L` on Mach-O and `readelf -d` on ELF, the tools the
platforms already require for a native link (zig cc on macOS prints its own
link table through the same Xcode toolchain). A platform without either
(a cross-compiled target the local tools cannot read, Windows PE) answers with
an empty list: the audit degrades to silence there rather than guessing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

#: libpython references reached through these prefixes are portable: the
#: relative rpath a bundle keeps relocatable, and the system library search
#: paths an OS provides. Everything else naming `libpython` is the host
#: interpreter's own library, linked by absolute path.
_PORTABLE_PREFIXES = ("@executable_path/", "@loader_path/", "@rpath/", "$ORIGIN/", "lib/", "/lib/", "/usr/lib/")

_LIBPYTHON_MACHO = re.compile(r"^\s+(?P<ref>\S*libpython\S*\.dylib)\s+\(")
_LIBPYTHON_ELF = re.compile(r"Shared library:\s+\[(?P<ref>libpython\S*)\]")
_RUNPATH_ELF = re.compile(r"(?:RUNPATH|RPATH)\)?\s+Library (?:runpath|rpath):\s+\[(?P<paths>[^\]]*)\]")

AuditTool = Callable[[Path], str]


class AuditError(Exception):
    """The audit tool ran and failed; the CLI prints it as a warning."""


def _is_host_reference(ref: str) -> bool:
    """True when the `libpython` reference names the host interpreter's library."""
    for prefix in _PORTABLE_PREFIXES:
        if ref.startswith(prefix):
            return False
    return True


def _elf_runpath_is_absolute(tool_output: str) -> bool:
    """True when the ELF binary's RUNPATH/RPATH is absolute (or absent).

    The ELF loader resolves a bare `NEEDED` soname through the binary's own
    runpath, so a relative (`$ORIGIN/...`) runpath means the `libpython`
    reference travels with the bundle. No runpath entry at all means the
    soname falls through to the host's system search path: the host link.
    """
    for line in tool_output.splitlines():
        match = _RUNPATH_ELF.search(line)
        if match is not None:
            for entry in match.group("paths").split(":"):
                entry = entry.strip()
                if entry.startswith("$ORIGIN") or entry.startswith("@"):
                    return False
                if entry.startswith("/"):
                    return True
            return False
    return True


def host_libpython_references(tool_output: str) -> list[str]:
    """The host-libpython references of an `otool -L` or `readelf -d` output.

    Handles both formats: each line is tried as Mach-O first, then as an ELF
    `NEEDED` entry. The audit feeds it one tool's output at a time, so the two
    never mix. A Mach-O reference reachable through the bundle's relative rpath
    or the system search paths is dropped; an ELF reference is the host's
    exactly when the binary's runpath is absolute or absent, because that is
    where the loader resolves the bare soname.
    """
    elf_absolute = _elf_runpath_is_absolute(tool_output)
    refs: list[str] = []
    for line in tool_output.splitlines():
        match = _LIBPYTHON_MACHO.match(line)
        if match is None:
            match = _LIBPYTHON_ELF.search(line)
            if match is not None and not elf_absolute:
                match = None
        if match is None:
            continue
        ref = match.group("ref")
        if _is_host_reference(ref):
            refs.append(ref)
    return refs


def _run_tool(argv: list[str], binary: Path) -> str:
    """Run `otool -L` / `readelf -d`; a failure is an AuditError, not a crash."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise AuditError(
            f"{' '.join(argv)} exited {proc.returncode} for {binary}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def audit_libpython(binary: Path, tool: AuditTool | None = None) -> list[str]:
    """The host-libpython references of the linked `binary`.

    Picks `otool -L` on macOS and `readelf -d` elsewhere; a platform with
    neither tool answers with an empty list (the audit cannot read the
    artifact there and degrades to silence instead of guessing). `tool` is the
    test seam: it receives the binary path and returns one tool's output.
    """
    if tool is not None:
        return host_libpython_references(tool(binary))
    argv: list[str] | None = None
    if sys.platform == "darwin":
        if shutil.which("otool") is not None:
            argv = ["otool", "-L", str(binary)]
    elif shutil.which("readelf") is not None:
        argv = ["readelf", "-d", str(binary)]
    if argv is None:
        return []
    return host_libpython_references(_run_tool(argv, binary))
