from __future__ import annotations

import sys

import pytest

from pyscriptc.errors import VersionError
from pyscriptc.ir import IR_VERSION, SUBSET, assert_same_cpython, cpython_version, target_cpython, target_minor
from pyscriptc.target import TARGET_CPYTHON, TARGET_MINOR, TARGET_VERSION_INFO


def test_target_declares_sanctioned_micro() -> None:
    assert TARGET_CPYTHON == "3.14.9"
    assert TARGET_MINOR == "3.14"
    assert TARGET_VERSION_INFO == (3, 14, 9)
    assert target_cpython() == "3.14.9"
    assert target_minor() == "3.14"


def test_cpython_version_matches_host() -> None:
    assert cpython_version() == sys.version.split()[0]
    assert cpython_version().startswith(target_minor())


def test_assert_same_cpython_accepts_target_stamp() -> None:
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": "3.14.9",
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    assert_same_cpython(module)


def test_assert_same_cpython_warns_on_micro_mismatch() -> None:
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": "3.14.8",
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    with pytest.warns(UserWarning, match="declared target is 3.14.9"):
        assert_same_cpython(module)


def test_assert_same_cpython_refuses_different_minor() -> None:
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": "3.13.9",
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    with pytest.raises(VersionError, match="declared target is 3.14.9"):
        assert_same_cpython(module)
