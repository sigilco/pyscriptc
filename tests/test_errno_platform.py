"""Platform only surface names are optional: a name the host lacks is left out, not fatal."""

from __future__ import annotations

import errno
import importlib
import os
import pathlib

import pytest

from pyscriptc import stdmore


def test_missing_errno_name_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(errno, "EADV", raising=False)
    found, _ = stdmore._load_constants()
    assert ("errno", "EADV") not in found
    assert ("errno", "ENOENT") in found


def test_missing_pathlib_errno_reexport_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(pathlib, "E2BIG", raising=False)
    found, _ = stdmore._load_constants()
    assert ("pathlib", "E2BIG") not in found


def test_missing_os_name_does_not_block_table_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "CLONE_FILES", raising=False)
    found, _ = stdmore._load_constants()
    assert ("os", "CLONE_FILES") not in found
    assert ("os", "O_RDONLY") in found


def test_every_int_name_the_host_defines_is_in_table() -> None:
    found, _ = stdmore._load_constants()
    for module, names in stdmore._INT_SURFACE.items():
        mod = importlib.import_module(module)
        for name in names:
            if hasattr(mod, name):
                assert (module, name) in found, f"{module}.{name}"
