"""Result cache for scorer v2.

Key = hash of (probe source and fields, oracle version, compiler files on the probe's
coverage map path). A probe with no coverage map entry falls back to the whole compiler
tree, so a missing map only costs cache hits and never gives a stale pass. Writes are
atomic: a temp file in the same directory, then `os.replace`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from benchmarks.compat2.outcomes import Probe

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPILER_DIR = ROOT / "src" / "pyscriptc"
CACHE_DIR = HERE / ".cache"
COMPILER_SUFFIXES = {".py", ".c", ".h"}

# Bump when the scorer changes what an outcome means. The oracle is CPython, so its
# interpreter version is part of the key too.
SCORER_VERSION = "2"


def oracle_version() -> str:
    return f"{SCORER_VERSION}/cpython-{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def compiler_files(modules: Iterable[str] | None = None) -> list[Path]:
    """Compiler sources, or only the listed ones (paths relative to `src/pyscriptc`)."""

    if modules is None:
        return sorted(p for p in COMPILER_DIR.rglob("*") if p.suffix in COMPILER_SUFFIXES)
    return sorted(COMPILER_DIR / m for m in set(modules) if (COMPILER_DIR / m).is_file())


def compiler_hash(modules: Iterable[str] | None = None) -> str:
    digest = hashlib.sha256()
    for file in compiler_files(modules):
        digest.update(str(file.relative_to(COMPILER_DIR)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def probe_hash(probe: Probe) -> str:
    return hashlib.sha256(json.dumps(probe.to_json(), sort_keys=True).encode()).hexdigest()


class FileHashes:
    """Hash each compiler file once per run, then combine per probe."""

    def __init__(self) -> None:
        self._file: dict[str, bytes] = {}
        self._combined: dict[tuple[str, ...], str] = {}
        self._all: str | None = None
        self._layout: str | None = None

    def layout(self) -> str:
        if self._layout is None:
            names = "\0".join(str(p.relative_to(COMPILER_DIR)) for p in compiler_files())
            self._layout = hashlib.sha256(names.encode()).hexdigest()
        return self._layout

    def whole(self) -> str:
        if self._all is None:
            self._all = compiler_hash()
        return self._all

    def of(self, modules: Iterable[str] | None) -> str:
        if modules is None:
            return self.whole()
        key = tuple(sorted(set(modules)))
        if key not in self._combined:
            digest = hashlib.sha256()
            # a new or renamed compiler file may belong on a probe's path, so the layout is part of every key
            digest.update(self.layout().encode())
            for name in key:
                if name not in self._file:
                    path = COMPILER_DIR / name
                    self._file[name] = hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b""
                digest.update(name.encode())
                digest.update(self._file[name])
            self._combined[key] = digest.hexdigest()
        return self._combined[key]


def cache_key(probe: Probe, coverage: dict[str, list[str]], hashes: FileHashes | str, oracle: str | None = None) -> str:
    """`hashes` may be a precomputed whole tree hash (the old call shape) or a FileHashes.

    `oracle` is the resolved oracle version string (same shape as `oracle_version()`);
    None means the interpreter running the scorer.
    """

    modules = coverage.get(probe.id)
    if isinstance(hashes, str):
        chash = hashes if modules is None else compiler_hash(modules)
    else:
        chash = hashes.of(modules)
    version = oracle if oracle is not None else oracle_version()
    return hashlib.sha256("\0".join((probe_hash(probe), version, chash)).encode()).hexdigest()


def load(cache_dir: Path, key: str) -> dict[str, Any] | None:
    path = cache_dir / f"{key}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # missing, or truncated by an interrupted run: recompute
    return data if isinstance(data, dict) else None


def store(cache_dir: Path, key: str, payload: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
