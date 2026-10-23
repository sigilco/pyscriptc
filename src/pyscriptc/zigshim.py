"""Locate a usable `zig` even when the user only ran `pip install ziglang`.

The `ziglang` PyPI wheel ships upstream Zig, but its console script is
`python-zig`, not `zig`, so `shutil.which("zig")` never finds a wheel-only
install. This module closes that gap:

1. If a real `zig` is on PATH, use it.
2. Otherwise install a `zig` (and on Windows `zig.bat`) shim into
   `~/.cache/pyscriptc/shim` that execs `python -m ziglang` with an
   interpreter that has the `ziglang` package, and prepend that directory to
   PATH for the current process, so the compiler and its children resolve
   `zig` by name.

No zig toolchain is pinned or downloaded here: whatever `ziglang` (or the
system install) provides is what runs. The supported version range lives in
`packaging/TOOLCHAIN.md` and is CI-verified on more than one zig version.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def shim_dir() -> Path:
    """Where the `zig` PATH shims live."""
    return Path.home() / ".cache" / "pyscriptc" / "shim"


def _python_has_ziglang(python: str) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", "import ziglang"],
            capture_output=True,
            timeout=60,
        )
    except Exception:
        return False
    return proc.returncode == 0


def find_ziglang_interpreter() -> str | None:
    """An interpreter whose `python -m ziglang` works.

    The running interpreter first (the common `uv tool install` or venv case,
    where pyscriptc and ziglang share an environment), then `python3` and
    `python` on PATH.
    """
    for python in (sys.executable, "python3", "python"):
        if python and _python_has_ziglang(python):
            return python
    return None


def _shim_text(python: str) -> str:
    return f'#!/bin/sh\nexec "{python}" -m ziglang "$@"\n'


def _shim_interpreter(path: str | Path) -> str | None:
    """The interpreter a shim execs, read back from the shim itself."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    marker = 'exec "'
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = text.find('"', start)
    if end < 0:
        return None
    return text[start:end]


def _valid_shim(path: str | Path) -> bool:
    """A shim whose interpreter can still run `python -m ziglang`.

    Shims outlive the environments that created them (a deleted venv leaves
    a shim pointing nowhere), so a found shim is probed before it is trusted.
    """
    python = _shim_interpreter(path)
    return python is not None and _python_has_ziglang(python)


def _bat_text(python: str) -> str:
    return f'@"{python}" -m ziglang %*\n'


def install_shim(python: str) -> Path:
    """Write the `zig` shim for `python` and make it executable."""
    directory = shim_dir()
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "zig"
    script.write_text(_shim_text(python), encoding="utf-8")
    script.chmod(0o755)
    if sys.platform == "win32":
        (directory / "zig.bat").write_text(_bat_text(python), encoding="utf-8")
    return script


def resolve_zig() -> str | None:
    """Path of a runnable `zig`, installing the PATH shim if needed.

    Returns the resolved path (a PATH hit or the installed shim), or None
    when no zig is available at all.
    """
    found = shutil.which("zig")
    if found:
        return found
    existing = shim_dir() / "zig"
    if existing.exists() and _valid_shim(existing):
        return str(existing)
    python = find_ziglang_interpreter()
    if python is None:
        return None
    return str(install_shim(python))


def ensure_on_path() -> bool:
    """Make `zig` resolvable by name for this process and its subprocesses.

    Installs the shim when needed and prepends its directory to PATH. A shim
    whose interpreter is gone is dropped from PATH and rebuilt, so a deleted
    venv cannot leave a broken `zig` on the path.
    Returns True when a zig is reachable afterwards.
    """
    directory = shim_dir()
    script = directory / "zig"
    if script.exists() and not _valid_shim(script):
        script.unlink(missing_ok=True)
    resolved = resolve_zig()
    entries = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    entries = [part for part in entries if Path(part).as_posix() != directory.as_posix()]
    if resolved is not None and Path(resolved).parent.as_posix() == directory.as_posix():
        entries.insert(0, str(directory))
    os.environ["PATH"] = os.pathsep.join(entries)
    return resolved is not None


def is_shim(path: str | None) -> bool:
    """True when `path` points at the installed shim rather than a real zig."""
    if not path:
        return False
    return Path(path).parent.as_posix() == shim_dir().as_posix()


def install_hint() -> str:
    """The user-facing fix for a missing zig."""
    return (
        "Install one of: `pip install ziglang` (the wheel ships zig and pyscriptc "
        "installs a `zig` PATH shim for it), `mise use -g zig`, `brew install zig`, "
        "`winget install zig`, or the download at https://ziglang.org/download/"
    )
