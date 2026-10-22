"""Issue #162: reserved coroutine exception codes leave user codes in place."""

from __future__ import annotations

import re
from pathlib import Path

from pyscriptc.builtins import EXCEPTIONS, RESERVED_EXC_BASE, RESERVED_EXCEPTIONS, USER_EXC_BASE

RUNTIME = Path(__file__).resolve().parent.parent / "src" / "pyscriptc" / "runtime"

C_NAMES = {
    "GeneratorExit": "PS_GENERATOR_EXIT",
    "StopAsyncIteration": "PS_STOP_ASYNC",
    "CancelledError": "PS_CANCELLED",
    "TimeoutError": "PS_TIMEOUT",
}


def _header_enum() -> dict[str, int]:
    text = (RUNTIME / "ps_rt.h").read_text()
    return {name: int(value, 0) for name, value in re.findall(r"\b(PS_[A-Z_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", text)}


def test_user_base_matches_header() -> None:
    # E2 M2 renumbered the builtin table to the CPython hierarchy, so user codes start at 256.
    header = _header_enum()
    assert header["PS_USER_BASE"] == 256
    assert USER_EXC_BASE == 256
    assert max(EXCEPTIONS.values()) == USER_EXC_BASE - 1


def test_reserved_codes_mirror_header() -> None:
    header = _header_enum()
    assert header["PS_RESERVED_BASE"] == RESERVED_EXC_BASE
    for name, code in RESERVED_EXCEPTIONS.items():
        assert header[C_NAMES[name]] == code


def test_reserved_codes_are_disjoint_and_out_of_user_range() -> None:
    codes = list(RESERVED_EXCEPTIONS.values())
    assert len(set(codes)) == 4
    assert min(codes) >= RESERVED_EXC_BASE > USER_EXC_BASE
    assert max(codes) < 2**31 - 1
    assert not set(codes) & set(EXCEPTIONS.values())


def test_runtime_names_reserved_codes() -> None:
    text = (RUNTIME / "ps_rt.c").read_text()
    for name, cname in C_NAMES.items():
        assert f'case {cname}:\n        return "{name}";' in text
