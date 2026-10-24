"""Content-hash binary cache behind `pyscriptc run`.

A binary is keyed on the source tree content, the compiler (version plus a digest of its own
code and runtime), the resolved config file and every flag that changes the output. It lives
under `$PYSC_CACHE_DIR` (default `~/.cache/pyscriptc`) in `bin/<key>/`, next to the `deps` and
`rt` caches. Entries are built in a temp dir and renamed into place, so concurrent runs never
see a partial binary. `PYSC_NO_RUN_CACHE=1` or `--no-cache` bypasses the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pyscriptc

_COMPILER_SUFFIXES = (".py", ".c", ".h", ".json", ".pyi")
_MEM_STAMP: str | None = None
_MEM_DIGEST: str | None = None


def cache_root() -> Path:
    base = os.environ.get("PYSC_CACHE_DIR")
    return Path(base) if base else Path.home() / ".cache" / "pyscriptc"


def cache_dir() -> Path:
    return cache_root() / "bin"


def enabled() -> bool:
    return os.environ.get("PYSC_NO_RUN_CACHE", "") not in ("1", "true", "yes")


def _compiler_stamp(root: Path) -> str:
    """A cheap stat-based stamp over the compiler package files and version."""
    root_str = str(root)
    root_len = len(root_str) + 1
    hasher = hashlib.sha256(pyscriptc.__version__.encode())
    entries: list[tuple[str, int, int]] = []
    for dirpath, _, filenames in os.walk(root_str):
        for fn in filenames:
            if fn.endswith(_COMPILER_SUFFIXES):
                p = os.path.join(dirpath, fn)
                st = os.stat(p)
                rel = p[root_len:]
                entries.append((rel, st.st_size, st.st_mtime_ns))
    entries.sort()
    for rel, size, mtime in entries:
        hasher.update(rel.encode())
        hasher.update(size.to_bytes(8, "little"))
        hasher.update(mtime.to_bytes(8, "little"))
    return hasher.hexdigest()


def _full_compiler_digest(root: Path) -> str:
    """Full content hash of every compiler file."""
    digest = hashlib.sha256(pyscriptc.__version__.encode())
    for path in sorted(p for p in root.rglob("*") if p.suffix in _COMPILER_SUFFIXES):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compiler_digest() -> str:
    """Version plus a digest of the compiler package (python, C, header and data files).

    Uses a fast stat-based stamp over the package files to avoid re-reading and
    hashing several megabytes on every invocation. When the stamp matches the
    cached value (in memory or in compiler_digest.json), the cached digest is returned.
    A full re-hash only runs on cache miss or when a file modification time or size changes.
    """
    global _MEM_STAMP, _MEM_DIGEST
    root = Path(pyscriptc.__file__).parent
    stamp = _compiler_stamp(root)
    if _MEM_STAMP == stamp and _MEM_DIGEST is not None:
        return _MEM_DIGEST

    stamp_path = cache_root() / "compiler_digest.json"
    if stamp_path.is_file():
        try:
            data = json.loads(stamp_path.read_text())
            if data.get("version") == pyscriptc.__version__ and data.get("stamp") == stamp:
                digest = data["digest"]
                _MEM_STAMP = stamp
                _MEM_DIGEST = digest
                return str(digest)
        except OSError, ValueError, KeyError:
            pass

    digest = _full_compiler_digest(root)
    try:
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = stamp_path.with_name(f".compiler_digest.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"version": pyscriptc.__version__, "stamp": stamp, "digest": digest}))
        tmp.replace(stamp_path)
    except OSError:
        pass

    _MEM_STAMP = stamp
    _MEM_DIGEST = digest
    return digest


def _tree_files(source: Path) -> list[Path]:
    if source.is_dir():
        return sorted(p for p in source.rglob("*.py") if p.is_file())
    # a single script may import sibling modules, so they count as content
    return sorted({source, *source.parent.glob("*.py")})


def key_for(source: Path, config_origin: Path | None, parts: list[str]) -> str:
    digest = hashlib.sha256(compiler_digest().encode())
    base = source if source.is_dir() else source.parent
    for path in _tree_files(source):
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    if config_origin is not None and config_origin.is_file():
        digest.update(config_origin.read_bytes())
    for part in parts:
        digest.update(b"\x01" + part.encode())
    return digest.hexdigest()[:32]


def binary_path(key: str, name: str) -> Path:
    return cache_dir() / key / name
