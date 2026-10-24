"""Issue #434 (D2): `__getattr__` reads lower to a direct hook call.

An unknown-name READ on a class whose MRO defines a plain
`def __getattr__(self, name: str) -> T` method lowers as a direct
two-argument call of the first definer, with the attribute name as a
compile-time string constant. Known fields, `@property` getters and
methods resolve first. The hook's declared return type must cover the
read site exactly: a mismatch, `None`, `bool` or an optional return is a
named `PS4204` fence, never a silent wrong-type load.

D1's refusal is lifted only for this one shape. A `@property` /
`@staticmethod` / `@classmethod` `__getattr__`, a generator hook, a wrong
parameter list, and every other hook in the `PS42xx` family keep
refusing at the definition site. Writes (`__setattr__`) are D3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

from pyscriptc.coverage import family_of
from pyscriptc.diagnostics import DIAGNOSTICS, explain

_PROXY_INNER = [
    "from __future__ import annotations",
    "",
    "",
    "class Inner:",
    "    value: int",
    "",
    "    def __init__(self, value: int) -> None:",
    "        self.value = value",
    "",
    "",
    "class Proxy:",
    "    x: int",
    "    inner: Inner",
    "",
    "    def __init__(self, x: int) -> None:",
    "        self.x = x",
    "        self.inner = Inner(x + 1)",
]


def _proxy_source(hook_lines: list[str], probe_lines: list[str]) -> str:
    return "\n".join(_PROXY_INNER + hook_lines + ["", "", "def probe() -> int:"] + probe_lines)


def test_unknown_read_calls_the_hook_with_the_constant_name(tmp_path: Path) -> None:
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return self.inner.value + len(name)",
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    print(p.x)",
            "    print(p.missing)",
            "    print(p.also_missing)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "hook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_the_read_site_return_type_must_cover(tmp_path: Path) -> None:
    """A str-returning hook feeding a str read matches CPython."""
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> str:",
            '        return "hook:" + name + str(self.inner.value)',
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    msg: str = p.whatever",
            "    print(msg)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "strhook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_a_return_type_mismatch_is_a_named_fence(tmp_path: Path) -> None:
    """A str hook feeding an int read site is PS4204, not a wrong-type load."""
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> str:",
            '        return "hook:" + name',
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    n: int = p.whatever",
            "    print(n)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "mismatch.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4204" in got.stderr, got.stderr
    assert "Proxy.__getattr__ returns str, the read site expects int" in got.stderr, got.stderr
    assert not (tmp_path / "out.ll").exists()


def test_a_none_return_never_covers_a_read(tmp_path: Path) -> None:
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> None:",
            '        print("hook ran")',
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    print(p.whatever)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "nonehook.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4204" in got.stderr, got.stderr
    assert "a read needs a value type" in got.stderr, got.stderr


def test_an_optional_return_is_refused(tmp_path: Path) -> None:
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int | None:",
            "        return None",
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    print(p.whatever)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "opthook.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4204" in got.stderr, got.stderr
    assert "the return type must cover the read site exactly" in got.stderr, got.stderr


def test_the_condition_context_expects_bool(tmp_path: Path) -> None:
    """`if c.flag` passes its bool expectation down: an int hook fences there."""
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return 5",
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    if p.check:",
            "        print(1)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "cond.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4204" in got.stderr, got.stderr
    assert "the read site expects bool" in got.stderr, got.stderr


def test_a_bool_hook_in_a_condition_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class Ctx:",
            "",
            "    def __init__(self) -> None:",
            "        pass",
            "",
            "    def __getattr__(self, name: str) -> bool:",
            "        return False",
            "",
            "",
            "def probe() -> int:",
            "    c: Ctx = Ctx()",
            "    if c.flag:",
            "        print(1)",
            "    else:",
            "        print(2)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "boolhook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_known_fields_properties_and_methods_resolve_first(tmp_path: Path) -> None:
    """The hook only takes names nothing else resolves."""
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class Proxy:",
            "    x: int",
            "",
            "    def __init__(self, x: int) -> None:",
            "        self.x = x",
            "",
            "    @property",
            "    def doub(self) -> int:",
            "        return self.x * 2",
            "",
            "    def helper(self) -> int:",
            "        return self.x + 1",
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return 1000",
            "",
            "",
            "def probe() -> int:",
            "    p: Proxy = Proxy(21)",
            "    print(p.x)",
            "    print(p.doub)",
            "    print(p.helper())",
            "    print(p.other)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "known.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_the_first_mro_definer_wins(tmp_path: Path) -> None:
    """A subclass without its own hook dispatches to the earliest ancestor."""
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
            "    def __getattr__(self, name: str) -> int:",
            "        return 42",
            "",
            "",
            "class Mid(Base):",
            "    y: int",
            "",
            "    def __init__(self, x: int, y: int) -> None:",
            "        super().__init__(x)",
            "        self.y = y",
            "",
            "",
            "class Leaf(Mid):",
            "    z: int",
            "",
            "    def __init__(self, x: int, y: int, z: int) -> None:",
            "        super().__init__(x, y)",
            "        self.z = z",
            "",
            "",
            "class Sibling(Base):",
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return 7",
            "",
            "",
            "def probe() -> int:",
            "    l: Leaf = Leaf(1, 2, 3)",
            "    print(l.nope)",
            "    s: Sibling = Sibling(5)",
            "    print(s.nope)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "mro.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_getattr_with_a_literal_name_rides_the_hook(tmp_path: Path) -> None:
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return len(name) + self.x",
        ],
        [
            "    p: Proxy = Proxy(3)",
            "    a: int = getattr(p, 'abc')",
            "    print(a)",
            "    print(getattr(p, 'zz'))",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "getattrfn.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_the_hook_reads_self_state_and_calls_methods(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class Proxy:",
            "    x: int",
            "",
            "    def __init__(self, x: int) -> None:",
            "        self.x = x",
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return self.name_len(name) + self.x",
            "",
            "    def name_len(self, name: str) -> int:",
            "        return len(name)",
            "",
            "",
            "def probe() -> int:",
            "    p: Proxy = Proxy(1)",
            "    print(p.abc)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "selfhook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_a_class_typed_hook_returns_a_lowered_value(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from __future__ import annotations",
            "",
            "",
            "class Payload:",
            "    v: int",
            "",
            "    def __init__(self, v: int) -> None:",
            "        self.v = v",
            "",
            "",
            "class Proxy:",
            "    x: int",
            "",
            "    def __init__(self, x: int) -> None:",
            "        self.x = x",
            "",
            "    def __getattr__(self, name: str) -> Payload:",
            "        return Payload(self.x)",
            "",
            "",
            "def probe() -> int:",
            "    p: Proxy = Proxy(9)",
            "    got: Payload = p.anything",
            "    print(got.v)",
            "    return 0",
        ]
    )
    path = write_case(tmp_path, "classhook.py", source)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


_UNSHAPED = [
    (
        "property-hook",
        [
            "    @property",
            "    def __getattr__(self) -> int:",
            "        return 5",
        ],
        "PS2001",
    ),
    (
        "zero-arg-hook",
        [
            "    def __getattr__(self) -> int:",
            "        return 5",
        ],
        "PS2001",
    ),
    (
        "two-name-hook",
        [
            "    def __getattr__(self, name: str, extra: int) -> int:",
            "        return 5",
        ],
        "PS2001",
    ),
    (
        "non-str-name-hook",
        [
            "    def __getattr__(self, name: int) -> int:",
            "        return 5",
        ],
        "PS2001",
    ),
]


@pytest.mark.parametrize(("label", "hook_lines", "code"), _UNSHAPED)
def test_unsupported_hook_shapes_keep_the_refusal(tmp_path: Path, label: str, hook_lines: list[str], code: str) -> None:
    """The D2 lift covers one shape: a plain (self, name: str) method."""
    source = _proxy_source(
        hook_lines,
        [
            "    p: Proxy = Proxy(3)",
            "    print(p.whatever)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, f"{label}.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert code in got.stderr, got.stderr


def test_the_write_side_is_still_D3(tmp_path: Path) -> None:
    """`p.nothere = 3` with a __getattr__ hook does not become a hook write."""
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return 5",
        ],
        [
            "    p: Proxy = Proxy(1)",
            "    p.nothere = 3",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "write.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS2001" in got.stderr, got.stderr
    assert "Proxy.nothere is not a field" in got.stderr, got.stderr


def test_other_hooks_still_refuse_next_to_a_getattr(tmp_path: Path) -> None:
    """Lifting __getattr__ does not lift the rest of the PS42xx family."""
    source = _proxy_source(
        [
            "",
            "    def __getattr__(self, name: str) -> int:",
            "        return 5",
            "",
            "    def __delattr__(self, name: str) -> None:",
            "        pass",
        ],
        [
            "    p: Proxy = Proxy(1)",
            "    print(p.nothere)",
            "    return 0",
        ],
    )
    path = write_case(tmp_path, "delattr.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "out.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS4202" in got.stderr, got.stderr
    assert "the __delattr__ hook is refused by design" in got.stderr, got.stderr


def test_the_read_site_mismatch_has_its_own_diagnostic() -> None:
    assert "PS4204" in DIAGNOSTICS
    text = explain("PS4204")
    assert text is not None
    assert "PS4204" in text
    assert "Why:" in text
    assert "Fix:" in text


def test_the_mismatch_code_has_a_coverage_family() -> None:
    assert family_of("PS4204", "Proxy.__getattr__ returns str, the read site expects int") == "type-mismatch"
