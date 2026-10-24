"""Data-shaped scaling probes (#387).

Probes for data structures, serialization, hashing, and file I/O that lowered
in the v0.7.0 typed subset. Each probe takes an integer size `n` and returns
an integer result verified against the CPython oracle.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile


def dict_grow(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i * 2
        i = i + 1
    total: int = 0
    for k in d.keys():
        total = total + k
    return total + len(d)


def set_grow(n: int) -> int:
    s: set[int] = set()
    i: int = 0
    while i < n:
        s.add(i)
        i = i + 1
    total: int = 0
    for item in s:
        total = total + item
    return total + len(s)


def dict_lookup(n: int) -> int:
    d: dict[int, int] = {}
    i: int = 0
    while i < n:
        d[i] = i
        i = i + 1
    hits: int = 0
    j: int = 0
    while j < n:
        if j in d:
            hits = hits + 1
        j = j + 1
    return hits + len(d)


def list_append(n: int) -> int:
    xs: list[int] = []
    i: int = 0
    while i < n:
        xs.append(i)
        i = i + 1
    total: int = 0
    for x in xs:
        total = total + x
    return total + len(xs)


def json_loads(n: int) -> int:
    d: dict[str, int] = {}
    i: int = 0
    while i < n:
        d[str(i)] = i
        i = i + 1
    raw: str = json.dumps(d)
    parsed: dict[str, int] = json.loads(raw)
    return len(parsed)


def str_build(n: int) -> int:
    parts: list[str] = []
    i: int = 0
    while i < n:
        parts.append("a")
        i = i + 1
    s: str = "".join(parts)
    return len(s)


def copyfile(n: int) -> int:
    d: str = tempfile.mkdtemp()
    src: str = os.path.join(d, "src.bin")
    dst: str = os.path.join(d, "dst.bin")
    with open(src, "wb") as handle:
        handle.write(b"a" * n)
    shutil.copyfile(src, dst)
    size: int = os.path.getsize(dst)
    shutil.rmtree(d)
    return size


def sha256_blocks(n: int) -> int:
    data: bytes = b"x" * (n * 64)
    digest: str = hashlib.sha256(data).hexdigest()
    return len(digest)
