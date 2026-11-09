"""Content-addressed cache of compiled runtime objects.

Every compile used to rebuild the same ~25 runtime `.c` files serially. Each is now
built once per (source + headers + compiler identity + target + flags) key under the
user cache dir and reused. Missing objects build in parallel, written to a temp file and
renamed into place, so concurrent compiles never see a partial object.
`PYSC_NO_RT_CACHE=1` bypasses the cache (objects are then built next to the output).

The `cc --version` probe behind the compiler-identity part of the key costs a
subprocess per compile, so its answer persists in a `ccid-*` sidecar keyed by
resolved binary path + mtime + size; a warm cache answers with a read and a
stat. `PYSC_NO_CC_ID_CACHE=1` forces the probe every time.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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


def _cc_id_cache_enabled() -> bool:
    return os.environ.get("PYSC_NO_CC_ID_CACHE", "") not in ("1", "true", "yes")


def _probe_compiler(cc: tuple[str, ...]) -> str:
    try:
        proc = subprocess.run([*cc, "--version"], capture_output=True, text=True, timeout=30)
    except OSError, subprocess.SubprocessError:
        return "unknown:" + " ".join(cc)
    return " ".join(cc) + "\n" + proc.stdout + proc.stderr


def _ccid_path(resolved: str, cc: tuple[str, ...]) -> Path:
    key = resolved + "\x01" + "\x01".join(cc)
    return cache_dir() / f"ccid-{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _read_ccid(path: Path, resolved: str, stamp: os.stat_result) -> str | None:
    """The persisted identity when the sidecar matches the binary's (mtime, size)."""
    try:
        head, marker, identity = path.read_text(encoding="utf-8").partition("\n---\n")
    except OSError:
        return None
    fields = head.split("\n")
    if not marker or len(fields) != 3:
        return None
    if fields[0] != resolved or fields[1] != str(stamp.st_mtime_ns) or fields[2] != str(stamp.st_size):
        return None
    return identity


def _write_ccid(path: Path, resolved: str, stamp: os.stat_result, identity: str) -> None:
    """Persist the probe result atomically; a read-only cache dir just skips it."""
    body: str = f"{resolved}\n{stamp.st_mtime_ns}\n{stamp.st_size}\n---\n{identity}"
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".ccid-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp, path)
    except OSError:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


@cache
def compiler_identity(cc: tuple[str, ...]) -> str:
    """The toolchain's `cc --version` identity, answered from a sidecar when fresh.

    The sidecar is keyed by the resolved binary path and validated by
    (mtime, size), so a swapped or upgraded toolchain re-probes instead of
    reusing the stale identity. Concurrent compiles either read the complete
    file or re-probe and atomically replace it; both orders are safe.
    """
    resolved = shutil.which(cc[0]) if _cc_id_cache_enabled() else None
    if resolved is None:
        return _probe_compiler(cc)
    try:
        stamp = os.stat(resolved)
    except OSError:
        return _probe_compiler(cc)
    identity = _read_ccid(_ccid_path(resolved, cc), resolved, stamp)
    if identity is not None:
        return identity
    identity = _probe_compiler(cc)
    if not identity.startswith("unknown:"):
        _write_ccid(_ccid_path(resolved, cc), resolved, stamp, identity)
    return identity


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


def extra_headers_digest(dirs: tuple[Path, ...]) -> str:
    """The digest of the `*.h` files in the include dirs of a manifest unit.

    A manifest unit compiles against the headers next to the manifest, not
    only against the runtime's own, so the object cache has to see them: a
    changed vendored header must not reuse the object built against the old
    one.
    """
    digest = hashlib.sha256()
    for directory in dirs:
        if not directory.is_dir():
            continue
        for header in sorted(directory.glob("*.h")):
            digest.update(str(header).encode())
            digest.update(b"\0")
            digest.update(header.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def key_for(
    source: Path,
    runtime: Path,
    cc: list[str],
    flags: list[str],
    extra_dirs: tuple[Path, ...] = (),
) -> str:
    digest = hashlib.sha256()
    for part in (
        source.name,
        hashlib.sha256(source.read_bytes()).hexdigest(),
        headers_digest(runtime),
        extra_headers_digest(extra_dirs),
        compiler_identity(tuple(cc)),
        "\0".join(flags),
    ):
        digest.update(part.encode())
        digest.update(b"\x01")
    return digest.hexdigest()[:32]


def object_path(
    source: Path, runtime: Path, cc: list[str], flags: list[str], extra_dirs: tuple[Path, ...] = ()
) -> Path:
    return cache_dir() / f"{source.stem}-{key_for(source, runtime, cc, flags, extra_dirs)}.o"


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
