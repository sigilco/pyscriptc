"""E1 item 1: module-level statements lower into a synthesized entry function.

The precision requirement: a definition-only module produces byte-identical IR,
which means no entry function exists at all. A script gets one entry named
`main`, or `__entry__` when the module defines its own `main`; a `__main__`
guard's body joins the entry in source order. A `print` statement is still an
entry statement, but it emits no body node and a fence until item 2 lands.
"""

from __future__ import annotations

from pyscriptc.canonical import hash_module
from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def test_definition_only_module_synthesizes_no_entry() -> None:
    mod = lower_source(
        """
import math

K: int = 3
S = "hi"

def probe() -> int:
    return K


if __name__ == "__main__":
    pass
"""
    )
    assert mod["entry"] is not None  # first function, the pre-E1 shape
    assert "main" not in {fn["name"] for fn in mod["functions"]}
    assert "__entry__" not in {fn["name"] for fn in mod["functions"]}
    assert mod["fences"] == []
    # Byte-identical with the pre-E1 module: a frozen probe shape.
    assert hash_module(mod) == "c639ef8d1cb2c9d2ec879ea3e6cc9be5870baa179f0facf0ac5cb1511b645072"


def test_simple_script_produces_entry_in_source_order() -> None:
    mod = lower_source(
        """
def f(x: int) -> int:
    return x * 2


for n in range(3):
    a: int = f(n)
    b: int = f(a + 1)
"""
    )
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    assert entry["params"] == [] and entry["returnType"] == {"kind": "none"}
    # The for lowers to slot init plus a while; the loop body binds a then b
    # per iteration, in source order.
    assert [local["name"] for local in entry["locals"]][:1] == ["n"]
    assert [local["name"] for local in entry["locals"]][-2:] == ["a", "b"]
    found: list[str] = []

    def walk(stmts: list[dict]) -> None:
        for stmt in stmts:
            if stmt["kind"] == "assign" and stmt.get("target") in {"a", "b"}:
                found.append(stmt["target"])
            for key in ("body", "then", "else"):
                if isinstance(stmt.get(key), list):
                    walk(stmt[key])

    walk(entry["body"])
    assert found == ["a", "b", "a", "b"]  # both while legs, a before b
    # Definitions first, then the entry.
    assert [fn["name"] for fn in mod["functions"]] == ["f", "main"]
    assert mod["fences"] == []


def test_main_guard_folds_into_the_entry() -> None:
    mod = lower_source(
        """
def f(x: int) -> int:
    return x * 2


if __name__ == "__main__":
    for n in range(3):
        a: int = f(n)
"""
    )
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    assert [local["name"] for local in entry["locals"]][-1:] == ["a"]
    assert mod["fences"] == []


def test_guard_assign_only_stays_a_module() -> None:
    # Assignments are not script statements on their own: a module whose only
    # non-definition text is one assignment keeps the pre-E1 behavior - the
    # constant fence and no entry. A script needs a statement that runs.
    mod = lower_source(
        """
def f() -> int:
    return 1


if __name__ == "__main__":
    a: int = f()
"""
    )
    assert mod["entry"] == "f"
    assert "main" not in {fn["name"] for fn in mod["functions"]}
    assert "call to f is not lowered" in {x["message"] for x in mod["fences"]}


def test_user_defined_main_wins_entry_becomes_dunder_entry() -> None:
    mod = lower_source(
        """
def main() -> int:
    return 1


def helper() -> int:
    return 2


for i in range(1):
    r: int = helper()
"""
    )
    names = [fn["name"] for fn in mod["functions"]]
    assert names == ["main", "helper", "__entry__"]
    assert mod["entry"] == "__entry__"


def test_script_constants_before_the_entry_stay_constant() -> None:
    mod = lower_source(
        """
N: int = 100

def f(x: int) -> int:
    return x + N


for i in range(1):
    r: int = f(i)
"""
    )
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    # N const-binds (it did before E1); the for is the entry and its loop
    # body binds r.
    found_r: list[bool] = []

    def walk_r(stmts: list[dict]) -> None:
        for stmt in stmts:
            if stmt["kind"] == "assign" and stmt.get("target") == "r":
                found_r.append(True)
            for key in ("body", "then", "else"):
                if isinstance(stmt.get(key), list):
                    walk_r(stmt[key])

    walk_r(entry["body"])
    assert found_r
    assert mod["fences"] == []
    # The constant is still there for readers: f's body inlines it.
    f_fn = next(fn for fn in mod["functions"] if fn["name"] == "f")
    read = f_fn["body"][0]["value"]
    assert read["right"]["kind"] == "const" and read["right"]["value"] == "100"


def test_failed_constant_binding_joins_the_entry_in_a_script() -> None:
    mod = lower_source(
        """
def fact(n: int) -> int:
    r: int = 1
    return r


f: int = fact(30)
print(f)
"""
    )
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    assert [stmt["kind"] for stmt in entry["body"]] == ["assign"]
    assert "call to fact is not lowered" not in {f["message"] for f in mod["fences"]}


def test_assign_only_module_keeps_its_constant_fence() -> None:
    # A module whose only non-definition text is one assignment keeps the
    # pre-E1 behavior: the constant fence, no entry.
    mod = lower_source(
        """
ARCHS = {1 + 2, 4}

def probe() -> int:
    return 0
"""
    )
    assert mod["entry"] == "probe"
    assert "main" not in {fn["name"] for fn in mod["functions"]}
    assert "a module assignment must be a constant" in {f["message"] for f in mod["fences"]}


def test_guard_else_stays_dropped() -> None:
    # An `else` under a `__name__` test never runs as `__main__`: only the
    # guard body joins the entry, so no local b exists.
    mod = lower_source(
        """
def f(x: int) -> int:
    return x * 2


if __name__ == "__main__":
    for n in range(3):
        a: int = f(n)
else:
    b: int = f(1)
"""
    )
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    assert [local["name"] for local in entry["locals"]][-1:] == ["a"]
    assert "b" not in [local["name"] for local in entry["locals"]]
    assert mod["fences"] == []


def test_package_unit_never_synthesizes_an_entry() -> None:
    import ast

    from pyscriptc.lower import Lowerer

    source = """
def f() -> int:
    return 1


print(f())
"""
    lowerer = Lowerer("pkg.py", source)
    lowerer.module_name = "pkg.mod"
    mod = lowerer.module(ast.parse(source))
    names = {fn["name"] for fn in mod["functions"]}
    assert "main" not in names and "__entry__" not in names
