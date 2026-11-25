# SPDX-License-Identifier: Apache-2.0
"""Issue #436 (D3): `__setattr__` writes lower to a direct hook call.

A write on a class whose MRO defines a plain
`def __setattr__(self, name: str, value: T) -> None` method lowers as a
direct three-argument call of the first definer, with the attribute name
as a compile-time string constant and the value checked against the
hook's own value parameter type. The hook runs before any descriptor
lookup, the way CPython routes `c.a = v` through `__setattr__` first: a
write to a known field, an unknown name and a property name all ride the
same hook. A write to `self.<field>` inside the hook body bypasses the
hook (the issue's "writes inside the hook bypass it" contract), so the
hook body can store without re-entering itself.

D1's refusal is lifted only for this one shape. A decorated
`__setattr__`, a generator hook, a wrong parameter list and a non-`None`
return keep the PS4201 refusal at the definition site. A `@dataclass`
class refuses the hook outright (PS2002): the generated `__init__`
writes straight to the fields, where CPython routes every write through
the hook, so the two would silently diverge.

The committed differential probe
`probes/differential/setattr_hook.py` is the AGENTS.md
differential-probe row for the lowering; the tests here are the
pinned-oracle replays of the same surface plus the refusal boundary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, compile_run, run_cli, write_case
from tests.test_runtime_divergences import compile_and_run

from pyscriptc.coverage import family_of
from pyscriptc.diagnostics import DIAGNOSTICS, explain

PROBE = ROOT / "probes" / "differential" / "setattr_hook.py"


def _oracle() -> str:
    """The pinned CPython 3.14 interpreter, when it is on this machine."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    return sys.executable


_PROXY = [
    "from __future__ import annotations",
    "",
    "",
    "class Proxy:",
    "    x: int",
    "",
    "    def __init__(self, x: int) -> None:",
    "        self.x = x",
    "",
]


def _proxy_source(hook_lines: list[str], probe_lines: list[str]) -> str:
    return "\n".join(_PROXY + hook_lines + ["", "", "def probe() -> int:"] + probe_lines)


def test_the_committed_probe_matches_cpython(tmp_path: Path) -> None:
    """The differential probe: the compiled program prints what CPython prints."""
    source = PROBE.read_text(encoding="utf-8")
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    got = compile_and_run(tmp_path, "probe.py", source)
    want = subprocess.run(
        [_oracle(), str(path)],
        capture_output=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert got == (want.stdout, want.stderr, want.returncode)


def test_a_write_calls_the_hook_with_the_constant_name(tmp_path: Path) -> None:
    """The dispatch is the feature; the store is the D108 seam, so the
    expected output is spelled, not oracle-compared (CPython re-enters the
    hook on the body's self-write and raises RecursionError)."""
    source = _proxy_source(
        [
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            '        print("hook", name, value)',
            "        self.x = value",
        ],
        [
            "    p: Proxy = Proxy(1)",
            "    p.x = 5",
            "    print(p.x)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "hook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hook x 1\nhook x 5\n5\n0\n"


def test_the_hook_runs_before_the_property_setter(tmp_path: Path) -> None:
    """CPython routes `c.a = v` through `__setattr__` before the descriptor.
    The compiled hook body stores straight to the field (the D108 seam),
    so the property setter inside CPython's `object.__setattr__` does not
    run on the compiled side and the expected output is spelled."""
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class C:",
            "    _c: float",
            "",
            "    def __init__(self, c: float) -> None:",
            "        self._c = c",
            "",
            "    @property",
            "    def celsius(self) -> float:",
            "        return self._c",
            "",
            "    @celsius.setter",
            "    def celsius(self, value: float) -> None:",
            '        print("prop setter", value)',
            "        self._c = value",
            "",
            "    def __setattr__(self, name: str, value: float) -> None:",
            '        print("hook", name, value)',
            "        self._c = value",
            "",
            "",
            "def probe() -> int:",
            "    c: C = C(20.0)",
            "    c.celsius = 100.0",
            "    print(c.celsius)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "prop.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    # The hook runs; the property setter does not (the compiled hook body
    # stores straight to the field, the issue's bypass contract).
    assert got.stdout == "hook _c 20.0\nhook celsius 100.0\n100.0\n0\n"


def test_the_hook_value_type_is_checked(tmp_path: Path) -> None:
    """A write whose value does not match the hook's value type is a fence."""
    source = _proxy_source(
        [
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            "        self.x = value",
        ],
        [
            "    p: Proxy = Proxy(1)",
            '    p.x = "nope"',
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "mismatch.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS1003" in got.stderr, got.stderr
    assert not (tmp_path / "out.ll").exists()


def test_the_first_mro_definer_wins(tmp_path: Path) -> None:
    """A subclass without its own hook dispatches to the earliest ancestor.
    The store is the D108 seam, so the expected output is spelled."""
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class Base:",
            "    x: int",
            "",
            "    def __init__(self, x: int) -> None:",
            "        self.x = x",
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            '        print("base", name, value)',
            "        self.x = value",
            "",
            "",
            "class Leaf(Base):",
            "    y: int",
            "",
            "    def __init__(self, x: int, y: int) -> None:",
            "        super().__init__(x)",
            "        self.y = y",
            "",
            "",
            "def probe() -> int:",
            "    l: Leaf = Leaf(1, 2)",
            "    l.y = 9",
            "    print(l.y)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "mro.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    # The hook body spells `self.x = value`, so every write lands on `x`
    # (the compiled store target is the body's spelled field, not the
    # write site's name; CPython's `object.__setattr__` store is dynamic
    # and is the D108 seam). `Leaf.y` never receives a write, so it reads
    # its zero value.
    assert got.stdout == "base x 1\nbase y 2\nbase y 9\n0\n0\n"


def test_a_write_inside_the_hook_bypasses_it(tmp_path: Path) -> None:
    """`self.x = v` inside the hook stores straight to the field."""
    source = _proxy_source(
        [
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            '        print("hook", name, value)',
            "        self.x = value",
        ],
        [
            "    p: Proxy = Proxy(1)",
            "    p.x = 5",
            "    print(p.x)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "bypass.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    # The hook ran exactly twice (init and the write), no recursion.
    assert got.stdout == "hook x 1\nhook x 5\n5\n0\n"


_UNSHAPED = [
    (
        "property-hook",
        [
            "    @property",
            "    def __setattr__(self) -> None:",
            "        pass",
        ],
    ),
    (
        "zero-arg-hook",
        [
            "    def __setattr__(self) -> None:",
            "        pass",
        ],
    ),
    (
        "two-name-hook",
        [
            "    def __setattr__(self, name: str, value: int, extra: int) -> None:",
            "        pass",
        ],
    ),
    (
        "non-str-name-hook",
        [
            "    def __setattr__(self, name: int, value: int) -> None:",
            "        pass",
        ],
    ),
    (
        "non-none-return-hook",
        [
            "    def __setattr__(self, name: str, value: int) -> int:",
            "        return 0",
        ],
    ),
]


@pytest.mark.parametrize(("label", "hook_lines"), _UNSHAPED)
def test_unsupported_hook_shapes_keep_the_refusal(tmp_path: Path, label: str, hook_lines: list[str]) -> None:
    """The D3 lift covers one shape: a plain (self, name: str, value: T) -> None method."""
    source = _proxy_source(
        hook_lines,
        [
            "    p: Proxy = Proxy(1)",
            "    p.x = 5",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, f"{label}.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4201" in got.stderr, got.stderr
    assert "the __setattr__ hook is refused by design" in got.stderr, got.stderr


def test_a_dataclass_with_a_setattr_hook_is_refused(tmp_path: Path) -> None:
    """The generated `__init__` writes straight to the fields, bypassing the
    hook, where CPython routes every write through it: the combination is
    refused rather than silently diverged."""
    source = "\n".join(
        [
            "from dataclasses import dataclass",
            "",
            "",
            "@dataclass",
            "class Point:",
            "    x: int",
            "    y: int",
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            "        self.x = value",
            "",
            "",
            "def probe() -> int:",
            "    p: Point = Point(1, 2)",
            "    print(p.x)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "dc.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2002" in got.stderr, got.stderr
    assert "a dataclass does not take a written __setattr__" in got.stderr, got.stderr


def test_other_hooks_still_refuse_next_to_a_setattr(tmp_path: Path) -> None:
    """Lifting `__setattr__` does not lift the rest of the PS42xx family."""
    source = _proxy_source(
        [
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            "        self.x = value",
            "",
            "    def __delattr__(self, name: str) -> None:",
            "        pass",
        ],
        [
            "    p: Proxy = Proxy(1)",
            "    p.x = 5",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "delattr.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4202" in got.stderr, got.stderr
    assert "the __delattr__ hook is refused by design" in got.stderr, got.stderr


def test_the_refusal_code_is_registered_and_explained() -> None:
    assert "PS4201" in DIAGNOSTICS
    text = explain("PS4201")
    assert text is not None
    assert "PS4201" in text
    assert "Why:" in text
    assert "Fix:" in text


def test_the_refusal_code_has_a_coverage_family() -> None:
    assert family_of("PS4201", "the __setattr__ hook is refused by design") == "refused-dynamic-attrs"
