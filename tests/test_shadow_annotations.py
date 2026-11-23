"""Issue #1306: the `pyscriptc._annotations` shadow module.

`island` and `specialize` are the identity on plain CPython and PyPy, so a
decorated program runs unmodified without pyscriptc. Under the compiler the
module is annotation-only like `typing`: every import spelling binds, the
decorators erase with no PS2001/PS2002/PS3099 note, and a decorated program
lowers to the same TIR as the undecorated one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source
from pyscriptc.tirhash import hash_module_node


def _fences(source: str) -> list[tuple[str, str]]:
    return [(fence["code"], fence["message"]) for fence in lower_source(source, "case.py")["fences"]]


_DECORATED = """
from pyscriptc._annotations import island, specialize


@island
def f(x: int) -> int:
    return x + 1


@specialize
class C:
    @island
    def m(self) -> int:
        return 2


def probe() -> int:
    return f(1)
"""


def test_shadow_module_is_inert_on_cpython() -> None:
    from pyscriptc._annotations import island, specialize

    def fn(x: int) -> int:
        return x

    assert island(fn) is fn
    assert specialize(fn) is fn
    assert island.__module__ == "pyscriptc._annotations"
    assert specialize.__module__ == "pyscriptc._annotations"


def test_shadow_module_imports_no_pyscriptc_internals() -> None:
    import subprocess
    import sys

    code = (
        "import sys; import pyscriptc._annotations; print(sorted(n for n in sys.modules if n.startswith('pyscriptc.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['pyscriptc._annotations']"


def test_from_import_binds_and_decorators_erase() -> None:
    assert _fences(_DECORATED) == []


def test_aliased_module_import_binds() -> None:
    source = """
import pyscriptc._annotations as ann


@ann.island
def f(x: int) -> int:
    return x + 1


def probe() -> int:
    return f(1)
"""
    assert _fences(source) == []


def test_dotted_module_import_binds() -> None:
    source = """
import pyscriptc._annotations


@pyscriptc._annotations.specialize
def f(x: int) -> int:
    return x


def probe() -> int:
    return f(1)
"""
    assert _fences(source) == []


def test_nested_and_class_scope_decorators_erase() -> None:
    source = """
from pyscriptc._annotations import island, specialize


class C:
    @island
    def m(self) -> int:
        return 2


def outer() -> int:
    @specialize
    def inner() -> int:
        return 3
    return inner()


def probe() -> int:
    return C().m() + outer()
"""
    assert _fences(source) == []


def test_shadow_decorator_used_as_value_fences() -> None:
    source = """
from pyscriptc._annotations import island


def probe() -> int:
    return island(0)
"""
    fences = _fences(source)
    assert fences and fences[0][0] == "PS2002"


def test_decorated_lowers_identically_to_undecorated() -> None:
    plain = "\n".join(line for line in _DECORATED.splitlines() if line.strip() not in {"@island", "@specialize"})
    decorated = lower_source(_DECORATED, "case.py")
    undecorated = lower_source(plain, "case.py")
    assert decorated["fences"] == []
    assert undecorated["fences"] == []
    assert hash_module_node(decorated) == hash_module_node(undecorated)


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_decorated_program_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "shadow.py", _DECORATED)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "2"
