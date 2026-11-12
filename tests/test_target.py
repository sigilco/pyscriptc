from __future__ import annotations

import sys

import pytest

from pyscriptc.errors import VersionError
from pyscriptc.ir import (
    IR_VERSION,
    SUBSET,
    assert_same_cpython,
    cpython_version,
    running_minor,
    running_version,
    target_cpython,
    target_minor,
)
from pyscriptc.target import TARGET_CPYTHON, TARGET_MINOR, TARGET_VERSION_INFO


def _another_minor() -> str:
    """A minor that is not the running one, so the stamp is foreign."""
    running = running_minor()
    for candidate in ("3.11", "3.12", "3.13", "3.14", "3.15"):
        if candidate != running:
            return candidate
    raise AssertionError("no other minor to test with")


def test_target_declares_sanctioned_micro() -> None:
    assert TARGET_CPYTHON == "3.14.9"
    assert TARGET_MINOR == "3.14"
    assert TARGET_VERSION_INFO == (3, 14, 9)
    assert target_cpython() == "3.14.9"
    assert target_minor() == "3.14"


def test_cpython_version_matches_host() -> None:
    """`cpython_version()` reports the running interpreter, above the floor."""
    assert cpython_version() == sys.version.split()[0]
    assert sys.version_info >= (3, 11), "the host floor is 3.11"


def test_assert_same_cpython_accepts_the_running_host_stamp() -> None:
    """A stamp naming this interpreter is accepted."""
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": running_version(),
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    assert_same_cpython(module)


def test_assert_same_cpython_accepts_micro_mismatch() -> None:
    """A different micro of the running minor is still the same host."""
    running = running_version()
    major, minor, _rest = running.split(".")
    other_micro = "0" if running.split(".")[2] != "0" else "1"
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": f"{major}.{minor}.{other_micro}",
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    assert_same_cpython(module)


def test_assert_same_cpython_refuses_a_stamp_from_another_host() -> None:
    """The stamp must name the interpreter that lowered the module.

    The tool host may be 3.11 through 3.15 (see `ir.HOST_FLOOR`), so the
    stamp is not the declared target anymore: it is the interpreter that
    produced the IR. Emission refuses an IR stamped by a different minor
    than the emitter runs in, because the emitter derives semantics from
    the interpreter it runs in.
    """
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": _another_minor(),
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    with pytest.raises(VersionError, match="re-lower on this interpreter"):
        assert_same_cpython(module)


def test_assert_same_cpython_refuses_the_declared_target_as_a_stamp() -> None:
    """A 3.14 stamp is not accepted by a non-3.14 host either.

    The stamp is self-consistency, not a target check: a 3.15 host lowering
    stamps 3.15, and a 3.14 stamp would mean the IR came from somewhere
    else.
    """
    module = {
        "irVersion": IR_VERSION,
        "subset": SUBSET,
        "cpython": TARGET_CPYTHON,
        "sourceFile": "test.py",
        "functions": [],
        "classes": [],
        "fences": [],
    }
    if running_minor() == target_minor():
        assert_same_cpython(module)
        return
    with pytest.raises(VersionError, match="re-lower on this interpreter"):
        assert_same_cpython(module)
