"""Module-scope containers keep reference semantics (#512, #513, #520).

A module-scope `list` or `dict` takes a real global slot through the same
binding the threading records use, so the container the program holds is
one pointer for the life of the process. Every read sees that object:
aliasing, parameter passing, `list.sort`, and the dict and list mutators
all behave like CPython. Each acceptance case compares stdout, stderr and
the exit code against CPython byte for byte.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case


def differential(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    """Compile, run, and pin the three observable channels to CPython."""
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / (name + ".bin"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (expected.returncode, expected.stdout, expected.stderr)
    return got


def test_alias_and_parameter_mutation(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "alias.py",
        (
            "xs: list[int] = [3, 1, 2]\n"
            "ys: list[int] = xs\n"
            "d: dict[str, list[int]] = {\"k\": [1, 2]}\n"
            "\n"
            "\n"
            "def bump(v: list[int]) -> None:\n"
            "    v[0] = 42\n"
            "\n"
            "\n"
            "def deep() -> None:\n"
            "    ys[0] = 99\n"
            "    d[\"k\"][0] = 99\n"
            "\n"
            "\n"
            "bump(xs)\n"
            "print(xs[0], ys[0])\n"
            "deep()\n"
            "print(ys[0], d[\"k\"][0])\n"
        ),
    )


def test_sort_on_module_scope_list(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "sort.py",
        (
            "xs: list[int] = [3, 1, 2]\n"
            "ys: list[float] = [2.5, 1.5]\n"
            "ws: list[str] = [\"b\", \"a\"]\n"
            "bs: list[bytes] = [b\"b\", b\"a\"]\n"
            "\n"
            "\n"
            "def f(ys: list[int]) -> int:\n"
            "    ys.sort(reverse=True)\n"
            "    return ys[0]\n"
            "\n"
            "\n"
            "print(f(xs))\n"
            "print(xs[0], xs[1], xs[2])\n"
            "ys.sort(reverse=True)\n"
            "ws.sort()\n"
            "bs.sort()\n"
            "print(ys[0], ws[0], bs[0])\n"
        ),
    )


def test_dict_setdefault_and_update(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "dictmut.py",
        (
            "d: dict[str, int] = {\"a\": 1}\n"
            "e: dict[str, int] = {\"b\": 2, \"c\": 5}\n"
            "\n"
            "\n"
            "def f() -> None:\n"
            "    d.setdefault(\"a\", 9)\n"
            "    d.setdefault(\"b\", 2)\n"
            "    d.update(e)\n"
            "    d.update({\"c\": 3})\n"
            "\n"
            "\n"
            "f()\n"
            "print(d[\"a\"], d[\"b\"], d[\"c\"])\n"
            "print(len(d))\n"
        ),
    )


def test_list_insert_extend_remove_reverse(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "listmut.py",
        (
            "xs: list[int] = [1, 3]\n"
            "ys: list[int] = [1, 2, 3]\n"
            "ws: list[str] = [\"a\", \"c\"]\n"
            "\n"
            "\n"
            "def f(v: list[int]) -> None:\n"
            "    v.insert(1, 2)\n"
            "    v.extend([4, 5])\n"
            "    v.remove(3)\n"
            "    v.reverse()\n"
            "\n"
            "\n"
            "f(xs)\n"
            "print(xs[0], xs[1], xs[2], xs[3], len(xs))\n"
            "ys.reverse()\n"
            "ws.extend([\"b\"])\n"
            "ws.insert(0, \"z\")\n"
            "ws.remove(\"c\")\n"
            "print(ys[0], ws[0], ws[1], ws[2], len(ws))\n"
        ),
    )


def test_module_scope_container_one_allocation(tmp_path: Path) -> None:
    from pyscriptc.emit.llvm import emit_llvm
    from pyscriptc.lower import lower_file

    path = write_case(tmp_path, "one.ll.py", "xs: list[int] = [1, 2]\n\nprint(xs[0])\nprint(xs[1])\n")
    mod, _ = lower_file(str(path))
    assert mod["fences"] == []
    assert mod.get("globals"), "the container binding hoists a global initializer"
    text = emit_llvm(mod, None, [], standalone=True)
    assert text.count("call ptr @ps_seq_new") == 1
    assert "call ptr @ps_global_get_ptr" in text


def test_definition_only_module_stays_definition_only() -> None:
    from pyscriptc.canonical import hash_module
    from pyscriptc.lower import lower_source

    mod = lower_source(
        """
K: int = 3
xs: list[int] = [1, 2]

def probe() -> int:
    return K
"""
    )
    assert mod["fences"] == []
    assert "main" not in {fn["name"] for fn in mod["functions"]}
    assert "__entry__" not in {fn["name"] for fn in mod["functions"]}
    assert mod.get("globals"), "the container slot initializer exists without an entry"


def test_assert_raises_assertionerror(tmp_path: Path) -> None:
    differential(
        tmp_path,
        "assert.py",
        (
            "try:\n"
            "    assert 1 == 2, 'one is not two'\n"
            "except AssertionError:\n"
            "    print('caught AssertionError')\n"
            "except Exception:\n"
            "    print('caught plain Exception')\n"
            "\n"
            "flag: bool = False\n"
            "try:\n"
            "    assert flag\n"
            "except AssertionError:\n"
            "    print('caught bare')\n"
        ),
    )
    import subprocess
    import sys

    path = write_case(tmp_path, "assert_uncaught.py", "assert 1 == 2, 'one is not two'\n")
    out = str(tmp_path / "assert_uncaught.bin")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run([sys.executable, "-c", path.read_text()], capture_output=True, text=True, check=False)
    assert got.returncode == expected.returncode == 1
    assert "AssertionError: one is not two" in got.stderr
