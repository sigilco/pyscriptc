"""Lowering a package tree must not leave file handles open."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyscriptc.package import lower_directory

ROOT = Path(__file__).resolve().parents[1]
FD_DIR = Path("/proc/self/fd")


@pytest.mark.skipif(not FD_DIR.is_dir(), reason="needs /proc")
def test_lowering_a_tree_does_not_leak_fds() -> None:
    tree = ROOT / "benchmarks" / "compat" / "top100" / "src" / "shellingham"
    lower_directory(tree)
    before = len(os.listdir(FD_DIR))
    for _ in range(10):
        lower_directory(tree)
    assert len(os.listdir(FD_DIR)) <= before
