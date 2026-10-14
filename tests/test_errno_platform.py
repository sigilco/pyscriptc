"""Platform only errno names are optional: a missing one is left out, not fatal."""

from __future__ import annotations

import errno
import pathlib

import pytest

from pyscriptc import stdmore


def test_missing_errno_name_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(errno, "EADV", raising=False)
    assert stdmore._host_int(errno, "EADV") is None
    assert stdmore._host_int(errno, "ENOENT") == errno.ENOENT


def test_pathlib_errno_reexport_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(pathlib, "E2BIG", raising=False)
    assert stdmore._host_int(pathlib, "E2BIG") is None


def test_present_errno_names_keep_their_values() -> None:
    for name in stdmore._ERRNO:
        if hasattr(errno, name):
            assert stdmore._host_int(errno, name) == int(getattr(errno, name))
