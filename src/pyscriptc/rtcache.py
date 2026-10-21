"""Content-addressed cache of compiled runtime objects.

Every compile used to rebuild the same ~25 runtime `.c` files serially. Each is now
built once per (source + headers + compiler identity + target + flags) key under the
user cache dir and reused. Missing objects build in parallel, written to a temp file and
renamed into place, so concurrent compiles never see a partial object.
`PYSC_NO_RT_CACHE=1` bypasses the cache (objects are then built next to the output).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

MAX_WORKERS = 8


def enabled() -> bool:
    return os.environ.get("PYSC_NO_RT_CACHE", "") not in ("1", "true", "yes")


def cache_dir() -> Path:
    override = os.environ.get("PYSC_RT_CACHE_DIR")
    return Path(override) if override else Path.home() / ".cache" / "pyscriptc" / "rt"


def stats() -> dict[str, object]:
    root = cache_dir()
    files = list(root.glob("*.o")) if root.is_dir() else []
    return {
        "enabled": enabled(),
        "dir": str(root),
        "entries": len(files),
        "bytes": sum(f.stat().st_size for f in files if f.exists()),
    }


@cache
def compiler_identity(cc: tuple[str, ...]) -> str:
    try:
        proc = subprocess.run([*cc, "--version"], capture_output=True, text=True, timeout=30)
    except OSError, subprocess.SubprocessError:
        return "unknown:" + " ".join(cc)
    return " ".join(cc) + "\n" + proc.stdout + proc.stderr


@cache
def _headers_digest(runtime: Path, mtimes: tuple[tuple[str, int, int], ...]) -> str:
    digest = hashlib.sha256()
    for name, _, _ in mtimes:
        digest.update(name.encode())
        digest.update((runtime / name).read_bytes())
    return digest.hexdigest()


def headers_digest(runtime: Path) -> str:
    stamp = tuple((h.name, h.stat().st_mtime_ns, h.stat().st_size) for h in sorted(runtime.glob("*.h")))
    return _headers_digest(runtime, stamp)


def key_for(source: Path, runtime: Path, cc: list[str], flags: list[str]) -> str:
    digest = hashlib.sha256()
    for part in (
        source.name,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        headers_digest(runtime),
        compiler_identity(tuple(cc)),
        "\0".join(flags),
    ):
        digest.update(part.encode())
        digest.update(b"\x01")
    return digest.hexdigest()[:32]


def object_path(source: Path, runtime: Path, cc: list[str], flags: list[str]) -> Path:
    return cache_dir() / f"{source.stem}-{key_for(source, runtime, cc, flags)}.o"


def run_jobs(jobs: list[tuple[list[str], Path | None]]) -> subprocess.CompletedProcess[str] | None:
    """Run compile jobs in parallel. Each is (argv, final_path). Output of each argv is `-o <tmp>`
    where argv already points at tmp; when final_path is set the tmp is renamed onto it.

    Returns the first failing process, else None."""
    if not jobs:
        return None

    def one(job: tuple[list[str], Path | None]) -> subprocess.CompletedProcess[str]:
        argv, final = job
        proc = subprocess.run(argv, capture_output=True, text=True)
        if final is not None:
            tmp = Path(argv[argv.index("-o") + 1])
            if proc.returncode == 0:
                os.replace(tmp, final)
            else:
                tmp.unlink(missing_ok=True)
        return proc

    workers = max(1, min(len(jobs), os.cpu_count() or 2, MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for proc in pool.map(one, jobs):
            if proc.returncode != 0:
                return proc
    return None


def temp_for(final: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=f".{final.stem}.", suffix=".o.tmp", dir=final.parent)
    os.close(fd)
    return Path(name)


def prepare() -> bool:
    """Create the cache dir; False means unusable (read-only home): fall back to bypass."""
    if not enabled():
        return False
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(cache_dir(), os.W_OK)
