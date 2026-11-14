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
a `libpython` reference the loader resolves through the binary's own search
path travels with the binary when that path is relative, and resolves to the
build host's library when it is absolute. The rules, per format:

- Mach-O (`otool -L` plus `otool -l` for the rpaths): a reference by absolute
  path is the host's. A `@rpath/...` reference is the host's too unless every
  rpath the binary carries is relative: the loader searches the rpaths in
  order, so an absolute rpath (the host's `lib/`) resolves the soname there.
  `@executable_path/...` and `@loader_path/...` are relative by construction
  and travel with the binary.
- ELF (`readelf -d`): a `NEEDED` entry is a bare soname the loader resolves
  through the binary's `RUNPATH`/`RPATH`; an absolute runpath (or none at all,
  which leaves the system search path) means the host's library. A
  `$ORIGIN/...` runpath means the library travels with the bundle.

The system library search paths (`/usr/lib`, `/lib`, `/lib64`) are the
operating system's, not the build host's, so a reference there is not flagged.
The PyPy engine packs are excluded: `libpypy*-c` is not `libpython`, and its
link flags pin a relative rpath by construction.

The readers are `otool` on Mach-O and `readelf -d` on ELF, the tools the
platforms already require for a native link. A platform without either (a
cross-compiled target the local tools cannot read, Windows PE) answers with
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
_PORTABLE_PREFIXES = ("@executable_path/", "@loader_path/", "$ORIGIN/", "lib/", "/lib/", "/usr/lib/")

#: A Mach-O load command naming the interpreter's own library. Two shapes
#: exist: the plain `libpython3.14.dylib` of a venv or a python-build-standalone
#: install, and the framework build's `Python.framework/Versions/3.14/Python`,
#: whose library is the interpreter itself under another name.
_LIBPYTHON_MACHO = re.compile(r"^\s+(?P<ref>\S*libpython\S*\.dylib)\s+\(")
_FRAMEWORK_MACHO = re.compile(r"^\s+(?P<ref>\S*Python\.framework/\S*Python)\s+\(")
_LIBPYTHON_ELF = re.compile(r"Shared library:\s+\[(?P<ref>libpython\S*)\]")
_RUNPATH_ELF = re.compile(r"(?:RUNPATH|RPATH)\)?\s+Library (?:runpath|rpath):\s+\[(?P<paths>[^\]]*)\]")
_RPATH_MACHO = re.compile(r"^\s+path\s+(?P<path>\S+)\s+\(offset \d+\)\s*$")

AuditTool = Callable[[Path], str]


class AuditError(Exception):
    """The audit tool ran and failed; the CLI prints it as a warning."""


def _is_host_reference(ref: str) -> bool:
    """True when the `libpython` reference names the host interpreter's library."""
    for prefix in _PORTABLE_PREFIXES:
        if ref.startswith(prefix):
            return False
    return True


def _search_path_is_absolute(entries: list[str]) -> bool:
    """True when the loader's search path points outside the binary.

    An empty path means the loader falls back to the system's own search
    path, which is the host's: the reference resolves to whatever the target
    machine has, and the build host's own library is exactly what a `host`
    link names.
    """
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if entry.startswith("$ORIGIN") or entry.startswith("@"):
            return False
        if entry.startswith("/"):
            return True
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
            return _search_path_is_absolute(match.group("paths").split(":"))
    return True


def _macho_rpaths(load_output: str) -> list[str]:
    """The rpaths a Mach-O binary carries, from an `otool -l` output."""
    rpaths: list[str] = []
    for line in load_output.splitlines():
        match = _RPATH_MACHO.match(line)
        if match is not None:
            rpaths.append(match.group("path"))
    return rpaths


def host_libpython_references(
    tool_output: str,
    load_output: str = "",
) -> list[str]:
    """The host-libpython references of an `otool -L` or `readelf -d` output.

    Handles both formats: each line is tried as Mach-O first, then as an ELF
    `NEEDED` entry. The audit feeds it one tool's output at a time, so the two
    never mix. A Mach-O reference reachable through the bundle's relative rpath
    or the system search paths is dropped; an ELF reference is the host's
    exactly when the binary's runpath is absolute or absent, because that is
    where the loader resolves the bare soname.

    `load_output` is the `otool -l` output of the same binary: it carries the
    rpaths, and a `@rpath/...` reference is the host's library when one of
    them is an absolute path (the loader searches them in order).
    """
    elf_absolute = _elf_runpath_is_absolute(tool_output)
    macho_rpath_absolute = _search_path_is_absolute(_macho_rpaths(load_output)) if load_output else True
    refs: list[str] = []
    for line in tool_output.splitlines():
        match = _LIBPYTHON_MACHO.match(line)
        if match is None:
            match = _FRAMEWORK_MACHO.match(line)
        if match is not None:
            ref = match.group("ref")
            if ref.startswith("@rpath/"):
                # The soname is resolved through the binary's rpaths: they
                # decide whether it lands on the build host's library.
                if not macho_rpath_absolute:
                    continue
            elif not _is_host_reference(ref):
                continue
            refs.append(ref)
            continue
        match = _LIBPYTHON_ELF.search(line)
        if match is not None and elf_absolute:
            refs.append(match.group("ref"))
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

    Picks `otool` on macOS and `readelf -d` elsewhere; a platform with
    neither tool answers with an empty list (the audit cannot read the
    artifact there and degrades to silence instead of guessing). `tool` is the
    test seam: it receives the binary path and returns one tool's output.
    """
    if tool is not None:
        return host_libpython_references(tool(binary))
    if sys.platform == "darwin":
        if shutil.which("otool") is None:
            return []
        listed = _run_tool(["otool", "-L", str(binary)], binary)
        try:
            load = _run_tool(["otool", "-l", str(binary)], binary)
        except AuditError:
            load = ""
        return host_libpython_references(listed, load)
    if shutil.which("readelf") is None:
        return []
    return host_libpython_references(_run_tool(["readelf", "-d", str(binary)], binary))
