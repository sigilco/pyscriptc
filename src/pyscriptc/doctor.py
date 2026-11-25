"""`pyscriptc doctor`: report the toolchain the compiler would use."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from pyscriptc import rtcache, zigshim
from pyscriptc.check import _resolve_pyrefly
from pyscriptc.emit._ir import _flag_of, _name_list, _opt_int, _opt_str, _opt_str_or_none
from pyscriptc.log import log_dir


def _version(argv: list[str], flag: str = "--version") -> str | None:
    lines: list[str] = []
    try:
        proc = subprocess.run([*argv, flag], capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            lines = (proc.stdout or proc.stderr).strip().splitlines()
    except Exception:
        return None
    return lines[0] if lines else None


def _tool(name: str, flag: str = "--version") -> dict[str, Any]:
    path = shutil.which(name)
    return {"found": path is not None, "path": path, "version": _version([path], flag) if path else None}


def _zig_tool() -> dict[str, Any]:
    """Zig entry for doctor: like `_tool`, but the `ziglang` wheel counts.

    A wheel-only install (`pip install ziglang`) has no `zig` on PATH; the
    shim installed here makes `which("zig")` succeed afterwards. On Windows
    the resolution is the wheel's own `zig.exe`, so the wheel counts there
    too.
    """
    path = shutil.which("zig")
    if path is None:
        path = zigshim.resolve_zig()
        if path is not None:
            zigshim.ensure_on_path()
    return {
        "found": path is not None,
        "path": path,
        "version": _version([path], "version") if path else None,
        "via_shim": zigshim.is_shim(path),
        "via_wheel": zigshim.is_wheel_zig(path),
    }


def install_shape() -> str:
    """One of `binary`, `tool`, `pipx`, `source`, `wheel`."""
    if getattr(sys, "frozen", False):
        return "binary"
    prefix = Path(sys.prefix).as_posix()
    if "/uv/tools/" in prefix:
        return "tool"
    if "/pipx/venvs/" in prefix:
        return "pipx"
    try:
        raw = metadata.distribution("pyscriptc").read_text("direct_url.json")
        if raw and json.loads(raw).get("dir_info", {}).get("editable"):
            return "source"
    except metadata.PackageNotFoundError:
        return "source"
    return "wheel"


def default_driver() -> str:
    if sys.platform == "win32":
        return "zig"
    if shutil.which("zig") is not None:
        return "zig"
    # A wheel-only install (the `ziglang` wheel, no `zig` on PATH) counts:
    # the shim puts the wheel's zig on PATH for the run.
    if zigshim.resolve_zig() is not None:
        return "zig"
    if shutil.which("clang") is not None:
        return "clang"
    return "none"


def toolchain_fix() -> str:
    """The actionable fix when the host has no C toolchain.

    The shape decides the command: a uv/pipx tool environment needs the
    tool reinstalled with `--with ziglang` (a `pip install` into the ambient
    interpreter would not reach it), a plain wheel/venv install takes
    `pip install ziglang` in the same environment, and a checkout or the
    release binary has nothing to append to, so the generic list stands
    (issue #1291).
    """
    return zigshim.install_hint(install_shape())


def toolchain_command() -> str | None:
    """The one command that adds a toolchain to this install, or None.

    The machine-readable form of `toolchain_fix`: the exact one-liner for
    the install shape, without the prose around it. A checkout or the
    release binary has no such command (there is nothing to append to), so
    it is None there (issue #1291).
    """
    return zigshim.install_command(install_shape())


def toolchain_found(zig: dict[str, Any], clang: dict[str, Any]) -> bool:
    """True when the machine can link a binary: zig or clang resolves."""
    if _flag_of(zig, "found"):
        return True
    return _flag_of(clang, "found")


def default_target() -> str:
    driver = default_driver()
    driver_str = f"via {driver}" if driver != "none" else "no C toolchain found"
    return f"{platform.machine()}-{platform.system().lower()} (host, {driver_str})"


def oracle_state() -> dict[str, Any]:
    """The CPython oracle `verify` would use, and the fix when it is missing.

    `verify` needs a CPython 3.14 interpreter. When the host has none, the
    exact fix is `uv python install 3.14` (issue #1290), so doctor prints it
    instead of leaving the user to guess.
    """
    from pyscriptc import verify

    try:
        oracle = verify.find_oracle()
    except Exception as exc:
        return {
            "found": False,
            "path": None,
            "version": None,
            "targetVersion": verify.TARGET_CPYTHON,
            "fix": verify.ORACLE_INSTALL_FIX,
            "error": str(exc),
        }
    return {
        "found": True,
        "path": oracle.executable,
        "version": oracle.version,
        "targetVersion": verify.TARGET_CPYTHON,
        "fix": verify.ORACLE_INSTALL_FIX,
    }


def gather() -> dict[str, Any]:
    pyrefly_res: tuple[list[str], str] = _resolve_pyrefly()
    argv: list[str] = pyrefly_res[0]
    source: str = pyrefly_res[1]
    own: str = "unknown"
    try:
        own = metadata.version("pyscriptc")
    except metadata.PackageNotFoundError:
        pass
    info = {
        "pyscriptc": own,
        "python": platform.python_version(),
        "checker": {
            "name": "pyrefly",
            "argv": argv,
            "source": source,
            "version": _version(argv),
        },
        "driver": default_driver(),
        "clang": _tool("clang"),
        "zig": _zig_tool(),
        "cache_dir": str(Path.home() / ".cache" / "pyscriptc"),
        "rt_cache": rtcache.stats(),
        "log_dir": str(log_dir()),
        "default_target": default_target(),
        "install": install_shape(),
        "oracle": oracle_state(),
    }
    info["toolchain"] = {
        "found": toolchain_found(info["zig"], info["clang"]),
        "fix": toolchain_fix(),
        "command": toolchain_command(),
    }
    return info


def render(info: dict[str, Any]) -> str:
    def tool(entry: dict[str, Any]) -> str:
        if not _flag_of(entry, "found"):
            return "not found"
        path = _opt_str(entry, "path")
        version = _opt_str_or_none(entry, "version")
        return f"{path} ({version or 'version unknown'})"

    def zig_line(entry: dict[str, Any]) -> str:
        if not _flag_of(entry, "found"):
            return "not found"
        text = tool(entry)
        if _flag_of(entry, "via_shim"):
            text += " [PATH shim for `pip install ziglang`]"
        elif _flag_of(entry, "via_wheel"):
            text += " [ziglang wheel]"
        return text

    checker: dict[str, Any] = info["checker"]
    rc: dict[str, Any] = info["rt_cache"]
    driver = _opt_str(info, "driver") or "zig"
    driver_str = f"{driver} (preferred)" if driver != "none" else "none (install zig or clang)"
    psc: str = info["pyscriptc"]
    pyv: str = info["python"]
    install: str = info["install"]
    argv = _name_list(checker, "argv")
    cver = _opt_str_or_none(checker, "version")
    csrc = _opt_str(checker, "source")
    clang: dict[str, Any] = info["clang"]
    zig: dict[str, Any] = info["zig"]
    cache_dir = _opt_str(info, "cache_dir")
    rc_dir = _opt_str(rc, "dir")
    rc_log_dir = _opt_str(info, "log_dir")
    target = _opt_str(info, "default_target")
    toolchain: dict[str, Any] = info.get("toolchain", {})
    toolchain_fix_text = _opt_str(toolchain, "fix")
    if _flag_of(toolchain, "found"):
        toolchain_line = "ok"
    else:
        toolchain_line = f"missing; {toolchain_fix_text}"
    oracle: dict[str, Any] = info.get("oracle", {})
    fix: str = _opt_str(oracle, "fix") or "uv python install 3.14"
    if _flag_of(oracle, "found"):
        oracle_line = (
            f"{_opt_str(oracle, 'path')} ({_opt_str_or_none(oracle, 'version') or 'version unknown'}); "
            f"a different one: `{fix}`"
        )
    else:
        oracle_line = f"not found; run `{fix}`"
    return "\n".join(
        [
            f"pyscriptc {psc} (python {pyv})",
            f"install:        {install}",
            f"checker:        {' '.join(argv)} ({cver or 'version unknown'}), source: {csrc}",
            f"driver:         {driver_str}",
            f"clang:          {tool(clang)}",
            f"zig:            {zig_line(zig)}",
            f"toolchain:      {toolchain_line}",
            f"oracle:         {oracle_line}",
            f"cache dir:      {cache_dir}",
            f"rt cache:       {'on' if _flag_of(rc, 'enabled') else 'off (PYSC_NO_RT_CACHE)'}, "
            f"{rc_dir} ({_opt_int(rc, 'entries', 0)} objects, {_opt_int(rc, 'bytes', 0)} bytes)",
            f"log dir:        {rc_log_dir}",
            f"default target: {target}",
        ]
    )


def doctor_cmd(as_json: bool) -> int:
    info = gather()
    print(json.dumps(info, indent=2) if as_json else render(info))
    return 0
