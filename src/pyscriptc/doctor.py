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

from pyscriptc import rtcache
from pyscriptc.check import _resolve_pyrefly
from pyscriptc.log import log_dir


def _version(argv: list[str], flag: str = "--version") -> str | None:
    try:
        proc = subprocess.run([*argv, flag], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    return lines[0] if lines else None


def _tool(name: str, flag: str = "--version") -> dict[str, Any]:
    path = shutil.which(name)
    return {"found": path is not None, "path": path, "version": _version([path], flag) if path else None}


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
    argv, source = _resolve_pyrefly()
    try:
        own = metadata.version("pyscriptc")
    except metadata.PackageNotFoundError:
        own = "unknown"
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
        "zig": _tool("zig", "version"),
        "cache_dir": str(Path.home() / ".cache" / "pyscriptc"),
        "rt_cache": rtcache.stats(),
        "log_dir": str(log_dir()),
        "default_target": default_target(),
        "install": install_shape(),
    }


def render(info: dict[str, Any]) -> str:
    def tool(entry: dict[str, Any]) -> str:
        if not entry["found"]:
            return "not found"
        return f"{entry['path']} ({entry['version'] or 'version unknown'})"

    checker = info["checker"]
    rc = info["rt_cache"]
    driver = info.get("driver", "zig")
    driver_str = f"{driver} (preferred)" if driver != "none" else "none (install zig or clang)"
    return "\n".join(
        [
            f"pyscriptc {info['pyscriptc']} (python {info['python']})",
            f"install:        {info['install']}",
            f"checker:        {' '.join(checker['argv'])} ({checker['version'] or 'version unknown'}), "
            f"source: {checker['source']}",
            f"driver:         {driver_str}",
            f"clang:          {tool(info['clang'])}",
            f"zig:            {tool(info['zig'])}",
            f"cache dir:      {info['cache_dir']}",
            f"rt cache:      {'on' if rc['enabled'] else 'off (PYSC_NO_RT_CACHE)'}, "
            f"{rc['dir']} ({rc['entries']} objects, {rc['bytes']} bytes)",
            f"log dir:        {info['log_dir']}",
            f"default target: {info['default_target']}",
        ]
    )


def doctor_cmd(as_json: bool) -> int:
    info = gather()
    print(json.dumps(info, indent=2) if as_json else render(info))
    return 0
