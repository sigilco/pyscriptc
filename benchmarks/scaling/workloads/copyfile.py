"""Workload program for copyfile scaling probe (#387)."""

from __future__ import annotations

import os
import shutil
import tempfile


def bench(n: int) -> int:
    d: str = tempfile.mkdtemp()
    src: str = os.path.join(d, "src.bin")
    dst: str = os.path.join(d, "dst.bin")
    with open(src, "wb") as handle:
        handle.write(b"a" * n)
    shutil.copyfile(src, dst)
    size: int = os.path.getsize(dst)
    shutil.rmtree(d)
    return size


def copyfile(n: int) -> int:
    return bench(n)
