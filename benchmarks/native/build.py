"""Build libmix.so and the nativeprobe extension that wraps it."""

from __future__ import annotations

import subprocess
import sysconfig
from pathlib import Path

HERE = Path(__file__).resolve().parent


def products() -> tuple[Path, Path]:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    return HERE / "libmix.so", HERE / f"nativeprobe{suffix}"


def ensure() -> tuple[Path, Path]:
    lib, ext = products()
    sources = [HERE / "mix.c", HERE / "mix.h", HERE / "nativeprobe.c"]
    newest = max(path.stat().st_mtime for path in sources)
    if lib.exists() and ext.exists() and lib.stat().st_mtime >= newest and ext.stat().st_mtime >= newest:
        return lib, ext
    include = sysconfig.get_path("include")
    _run(["clang", "-shared", "-fPIC", "-O2", "-o", str(lib), str(HERE / "mix.c")])
    _run(
        [
            "clang",
            "-shared",
            "-fPIC",
            "-O2",
            f"-I{include}",
            "-o",
            str(ext),
            str(HERE / "nativeprobe.c"),
            f"-L{HERE}",
            "-lmix",
            f"-Wl,-rpath,{HERE}",
        ]
    )
    return lib, ext


def _run(command: list[str]) -> None:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
