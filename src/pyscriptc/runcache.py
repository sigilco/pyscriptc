"""Content-hash binary cache behind `pyscriptc run`.

A binary is keyed on the source tree content, the compiler (version plus a digest of its own
code and runtime), the resolved config file and every flag that changes the output. It lives
under `$PYSC_CACHE_DIR` (default `~/.cache/pyscriptc`) in `bin/<key>/`, next to the `deps` and
`rt` caches. Entries are built in a temp dir and renamed into place, so concurrent runs never
see a partial binary. `PYSC_NO_RUN_CACHE=1` or `--no-cache` bypasses the cache.
"""

from __future__ import annotations

import hashlib
import os
from functools import cache
from pathlib import Path

import pyscriptc


def cache_dir() -> Path:
    base = os.environ.get("PYSC_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".cache" / "pyscriptc") / "bin"


def enabled() -> bool:
    return os.environ.get("PYSC_NO_RUN_CACHE", "") not in ("1", "true", "yes")


@cache
def compiler_digest() -> str:
    """Version plus a digest of the compiler package (python, C, header and data files)."""
    root = Path(pyscriptc.__file__).parent
    digest = hashlib.sha256(pyscriptc.__version__.encode())
    for path in sorted(p for p in root.rglob("*") if p.suffix in (".py", ".c", ".h", ".json", ".pyi")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


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
