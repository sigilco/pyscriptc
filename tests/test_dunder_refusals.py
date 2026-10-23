"""Issue #432: the dynamic attribute protocol is refused by design (PS42xx).

A class that defines `__getattr__`, `__setattr__`, `__getattribute__`,
`__get__`/`__set__`, `__delattr__` or `__del__` used to compile, and the hook
was then silently dropped (`__del__` never ran) or silently bypassed
(`c.a = 1` wrote the field straight, the hook never ran). The refusal names
the hook at its definition site, so a library probe gets an honest signal.

`@property` getters are not refused: they lower and the getter is called at the
read site. The `PS42xx` range is the refusal range (never, and here is what to
write instead); `PS4001`-`PS4016` are the FFI manifest range.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

from pyscriptc.coverage import family_of
from pyscriptc.diagnostics import DIAGNOSTICS, explain


def _class_source(hook: str, body: list[str]) -> str:
    return "\n".join(
        [
            "class C:",
            "    a: int",
            "",
            "    def __init__(self, a: int) -> None:",
            "        self.a = a",
            "",
            f"    def {hook}({', '.join(body)}) -> None:",
            "        pass",
            "",
            "def run() -> int:",
            "    c: C = C(1)",
            "    print(c.a)",
            "    return 0",
            "",
        ]
    )


_HOOKS = [
    ("__getattr__", "PS4201", ["self", "name: str"]),
    ("__setattr__", "PS4201", ["self", "name: str", "value: int"]),
    ("__getattribute__", "PS4201", ["self", "name: str"]),
    ("__get__", "PS4201", ["self", "obj: int", "owner: int"]),
    ("__set__", "PS4201", ["self", "obj: int", "value: int"]),
    ("__delattr__", "PS4202", ["self", "name: str"]),
]


@pytest.mark.parametrize(("hook", "code", "params"), _HOOKS)
def test_hook_definition_is_refused_by_name(tmp_path: Path, hook: str, code: str, params: list[str]) -> None:
    source = _class_source(hook, params)
    path = write_case(tmp_path, f"{hook.strip('_')}.py", source)
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "out.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert code in got.stderr, got.stderr
    assert f"the {hook} hook is refused by design" in got.stderr, got.stderr


def test_finalizer_is_refused_by_name(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "class C:",
            "    a: int",
            "",
            "    def __init__(self, a: int) -> None:",
            "        self.a = a",
            "",
            "    def __del__(self) -> None:",
            "        print('del ran')",
            "",
            "def run() -> int:",
            "    c: C = C(1)",
            "    print(c.a)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "finalizer.py", source)
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "out.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert "PS4203" in got.stderr, got.stderr
    assert "the __del__ hook is refused by design" in got.stderr, got.stderr


def test_the_silent_setattr_trap_is_gone(tmp_path: Path) -> None:
    """The divergence this ticket kills: the hook compiled and never ran."""
    source = "\n".join(
        [
            "class C:",
            "    a: int",
            "",
            "    def __init__(self, a: int) -> None:",
            "        self.a = a",
            "",
            "    def __setattr__(self, name: str, value: int) -> None:",
            "        self.a = value",
            "",
            "def run() -> int:",
            "    c: C = C(1)",
            "    c.a = 5",
            "    print(c.a)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "setattr.py", source)
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "out.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert "PS4201" in got.stderr, got.stderr
    # The compile stops before emission: no binary that silently drops the hook.
    assert not (tmp_path / "out.ll").exists()


def test_a_property_getter_still_lowers(tmp_path: Path) -> None:
    """The refusal is on the hook, not on the property: getters lower."""
    source = "\n".join(
        [
            "class Box:",
            "    n: int",
            "",
            "    def __init__(self, n: int) -> None:",
            "        self.n = n",
            "",
            "    @property",
            "    def doubled(self) -> int:",
            "        return self.n + self.n",
            "",
            "def run() -> int:",
            "    b: Box = Box(3)",
            "    print(b.doubled)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "prop.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_a_plain_class_still_lowers(tmp_path: Path) -> None:
    """The fence is on the hook names only: an ordinary class is untouched."""
    source = "\n".join(
        [
            "class C:",
            "    a: int",
            "",
            "    def __init__(self, a: int) -> None:",
            "        self.a = a",
            "",
            "    def get(self) -> int:",
            "        return self.a",
            "",
            "def run() -> int:",
            "    c: C = C(1)",
            "    print(c.get())",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "plain.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_the_refusal_reaches_a_package_module(tmp_path: Path) -> None:
    """The hook is refused where it is defined, not only where it is used."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "\n".join(
            [
                "class C:",
                "    a: int",
                "",
                "    def __init__(self, a: int) -> None:",
                "        self.a = a",
                "",
                "    def __setattr__(self, name: str, value: int) -> None:",
                "        self.a = value",
                "",
            ]
        ),
        encoding="utf-8",
    )
    got = run_cli(
        "compile",
        str(pkg),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "out.ll"),
    )
    assert got.returncode == 3, got.stderr
    assert "PS4201" in got.stderr, got.stderr
    assert "mod.py" in got.stderr, got.stderr


def test_the_fence_has_its_own_coverage_family() -> None:
    assert family_of("PS4201", "the __setattr__ hook is refused by design") == "refused-dynamic-attrs"
    assert family_of("PS4202", "the __delattr__ hook is refused by design") == "refused-dynamic-attrs"
    assert family_of("PS4203", "the __del__ hook is refused by design") == "refused-finalizer"


def test_every_refusal_code_is_registered_and_explained() -> None:
    for code in ("PS4201", "PS4202", "PS4203"):
        assert code in DIAGNOSTICS
        text = explain(code)
        assert text is not None
        assert code in text
        assert "Why:" in text
        assert "Fix:" in text
    # The refusal range says never, not not-yet: the fix names the substitute.
    assert "annotated fields" in explain("PS4201")
    assert "close()" in explain("PS4203")


def test_coverage_names_the_refused_hook(tmp_path: Path) -> None:
    path = write_case(tmp_path, "cov.py", _class_source("__setattr__", ["self", "name: str", "value: int"]))
    got = run_cli("coverage", str(path))
    assert got.returncode == 0, got.stderr
    assert "PS4201" in got.stdout
    assert "family: refused-dynamic-attrs" in got.stdout
    assert "the __setattr__ hook is refused by design" in got.stdout
