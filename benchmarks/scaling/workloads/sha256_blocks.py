"""Workload program for sha256_blocks scaling probe (#387)."""

from __future__ import annotations

import hashlib


def bench(n: int) -> int:
    data: bytes = b"x" * (n * 64)
    digest: str = hashlib.sha256(data).hexdigest()
    return len(digest)


def sha256_blocks(n: int) -> int:
    return bench(n)
