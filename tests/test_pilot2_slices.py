# SPDX-License-Identifier: Apache-2.0
"""Issue #417: the pilot 2 (iniconfig) slices that lower today.

Two refusals the measured iniconfig 2.3.1 profile hit and that are small
enough to close inside the pilot PR:

- the bare `Final` qualifier (`UTF8_BOM: Final = "..."`, PS1103): it names
  no type, so it lowers as the plain assignment the checker infers from;
- `None` arms of a tuple display returned against an optional-field tuple
  annotation (`return None, x` under `-> tuple[str | None, str | None]`,
  PS1003): each element now lowers against its field's type, so the `None`
  boxes into the field's optional the same way a bare `return None` does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[tuple[str, str]]:
    return [(f["code"], f["message"]) for f in lower_source(source, "case.py")["fences"]]


@pytest.mark.parametrize(
    "header",
    [
        "from typing import Final\n",
        "from typing_extensions import Final\n",
        "import typing\n",
        "from typing import Final as Fin\n",
    ],
)
def test_bare_final_module_constant_lowers(header: str) -> None:
    spelling = {"import typing\n": "typing.Final", "from typing import Final as Fin\n": "Fin"}.get(header, "Final")
    source = header + f'BOM: {spelling} = "\\N{{BYTE ORDER MARK}}"\n\ndef f() -> int:\n    return len(BOM + "x")\n'
    assert _fences(source) == []


def test_bare_final_local_lowers() -> None:
    source = "from typing import Final\n\ndef f() -> int:\n    n: Final = 3\n    return n + 1\n"
    assert _fences(source) == []


def test_subscripted_final_is_untouched() -> None:
    # `Final[int]` already unwraps to its argument; the rewrite leaves it.
    source = "from typing import Final\n\nN: Final[int] = 3\n\ndef f() -> int:\n    return N\n"
    assert _fences(source) == []


def test_a_local_class_named_final_is_not_the_qualifier() -> None:
    # Only the typing spellings match: a user class called `Final` keeps
    # being a class annotation, so the rewrite does not erase it.
    source = "class Final:\n    pass\n\nX: Final = Final()\n\ndef f() -> int:\n    return 1\n"
    assert all("Final is not a lowered type" not in msg for _, msg in _fences(source))


TUPLE_OPT = """\
def f(x: int) -> tuple[str | None, str | None]:
    if x == 0:
        return None, None
    if x == 1:
        return "a", None
    if x == 2:
        return None, "b"
    return "c", "d"
"""


def test_none_arms_of_a_returned_tuple_box_into_the_fields() -> None:
    assert _fences(TUPLE_OPT) == []


def test_a_mismatched_tuple_arm_still_fences() -> None:
    source = "def f() -> tuple[str | None, int]:\n    return None, 'x'\n"
    assert [code for code, _ in _fences(source)] == ["PS1003"]


PROGRAM = (
    """\
from typing import Final

BOM: Final = "\\N{BYTE ORDER MARK}"


"""
    + TUPLE_OPT
    + """

def main() -> None:
    print(len(BOM + "x"))
    for i in range(4):
        a, b = f(i)
        print(a is None, b is None, a if a is not None else "-", b if b is not None else "-")


if __name__ == "__main__":
    main()
"""
)


def test_pilot2_slices_run_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "pilot2_slices.py", PROGRAM)
    out = str(tmp_path / "pilot2_slices")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    assert got.returncode == 0, got.stderr
    want = subprocess.run([sys.executable, "-I", str(path)], capture_output=True, text=True, check=True)  # noqa: S603
    assert got.stdout == want.stdout


def test_bare_final_in_a_plain_class_is_a_class_constant() -> None:
    source = (
        "from typing import Final\n\nclass Flags:\n    FROZEN: Final = 0\n    NEST: Final = 1\n\n"
        "def f() -> int:\n    return Flags.FROZEN + Flags.NEST\n"
    )
    assert _fences(source) == []


def test_bare_final_in_a_dataclass_keeps_its_annotation() -> None:
    # A dataclass reads `x: Final = 3` as a field with a default; the rewrite
    # must not turn it into a class attribute.
    source = "from dataclasses import dataclass\nfrom typing import Final\n\n@dataclass\nclass P:\n    x: Final = 3\n"
    assert any("Final" in msg for _, msg in _fences(source))
