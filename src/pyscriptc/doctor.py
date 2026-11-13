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
    """One of `binary`, `tool`, `source`, `wheel`."""
    if getattr(sys, "frozen", False):
        return "binary"
    prefix = Path(sys.prefix).as_posix()
    if "/uv/tools/" in prefix or "/pipx/venvs/" in prefix:
        return "tool"
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
    if shutil.which("clang") is not None:
        return "clang"
    return "none"


def default_target() -> str:
    driver = default_driver()
    driver_str = f"via {driver}" if driver != "none" else "no C toolchain found"
    return f"{platform.machine()}-{platform.system().lower()} (host, {driver_str})"


def gather() -> dict[str, Any]:
    pyrefly_res: tuple[list[str], str] = _resolve_pyrefly()
    argv: list[str] = pyrefly_res[0]
    source: str = pyrefly_res[1]
    own: str = "unknown"
    try:
        own = metadata.version("pyscriptc")
    except metadata.PackageNotFoundError:
        pass
    return {
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
    }


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
    return "\n".join(
        [
            f"pyscriptc {psc} (python {pyv})",
            f"install:        {install}",
            f"checker:        {' '.join(argv)} ({cver or 'version unknown'}), source: {csrc}",
            f"driver:         {driver_str}",
            f"clang:          {tool(clang)}",
            f"zig:            {zig_line(zig)}",
            f"cache dir:      {cache_dir}",
            f"rt cache:      {'on' if _flag_of(rc, 'enabled') else 'off (PYSC_NO_RT_CACHE)'}, "
            f"{rc_dir} ({_opt_int(rc, 'entries', 0)} objects, {_opt_int(rc, 'bytes', 0)} bytes)",
            f"log dir:        {rc_log_dir}",
            f"default target: {target}",
        ]
    )


def doctor_cmd(as_json: bool) -> int:
    info = gather()
    print(json.dumps(info, indent=2) if as_json else render(info))
    return 0
