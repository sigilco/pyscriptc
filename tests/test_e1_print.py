"""E1 item 2: `print` lowers to a runtime stdout write.

The compiler routes `print` through the builtin call surface (restlower) with
a closed value set: str, int, float, bool, space-separated, newline ended.
`sep=`, `end=`, and `file=` stay fenced. Both the synthesized entry and
regular functions carry the write. Float text is the CPython `str(float)`
spelling via the same runtime conversion `str()` uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case
from tests.test_spike import ROOT

from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def test_print_single_and_multi_arg_lower_without_fences() -> None:
    mod = lower_source(
        """
def shout(text: str) -> None:
    print(text)
    print(text, 42, 1.5, True)


print("hello", 42)
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None and entry["name"] == "main"
    shout = next(fn for fn in mod["functions"] if fn["name"] == "shout")
    assert [stmt["kind"] for stmt in entry["body"]] == ["expr"]
    assert [stmt["kind"] for stmt in shout["body"]] == ["expr", "expr"]
    for stmt in [*entry["body"], *shout["body"]]:
        assert stmt["expr"]["callee"] == {"kind": "builtin", "name": "print"}
        assert stmt["expr"]["type"] == {"kind": "none"}


def test_print_in_entry_prints_like_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "script.py",
        "\n".join(
            [
                "def f(x: int) -> int:",
                "    return x * 2",
                "",
                "",
                'print("hello", 42)',
                "print(f(21))",
                'print("done")',
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "script"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello 42\n42\ndone\n"


def test_print_in_function_and_entry_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "mixed.py",
        "\n".join(
            [
                "def report(n: int) -> None:",
                "    print('n =', n, 1.5, True, False)",
                "",
                "",
                "print('start')",
                "report(7)",
                "print(-0.5, 300)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "mixed"))
    assert got.returncode == 0, got.stderr
    expect = "start\nn = 7 1.5 True False\n-0.5 300\n"
    assert got.stdout == expect == oracle_script(path)


def oracle_script(path: Path) -> str:
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def test_print_float_text_is_cpython_str(tmp_path: Path) -> None:
    # The float spelling comes from the same conversion str() uses:
    # shortest round-trip, always a .0 or an exponent. 0.1+0.2 must not
    # collapse, and the exponent form keeps CPython's two-digit e+NN.
    path = write_case(
        tmp_path,
        "floats.py",
        "\n".join(
            [
                "print(0.1 + 0.2)",
                "print(1.5, 2.0, -0.25)",
                "print(1e16)",
                "print(1e-5)",
                "print(2.5e-300)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "floats"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path)


def test_print_empty_call_prints_a_bare_newline(tmp_path: Path) -> None:
    path = write_case(tmp_path, "bare.py", "print()\nprint(1)\n")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "bare"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path)


def test_print_sep_end_kwargs_lower() -> None:
    for kw in ("sep='|'", "end=''"):
        mod = lower_source(f"print(1, {kw})\n")
        assert mod["fences"] == [], kw


def test_print_file_kwarg_fences() -> None:
    mod = lower_source("print(1, file=sys.stderr)\n")
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2002"
    assert "print file= takes sys.stdout or sys.stderr" in fence["message"]


def test_print_file_kwarg_lowers() -> None:
    for stream in ("sys.stdout", "sys.stderr"):
        mod = lower_source(f"import sys\nprint(1, file={stream})\n")
        assert mod["fences"] == [], stream


def test_print_value_outside_the_scalar_set_fences() -> None:
    mod = lower_source(
        """
items: list[int] = [1, 2]
print(items)
"""
    )
    assert len(mod["fences"]) == 1
    fence = mod["fences"][0]
    assert fence["code"] == "PS2003"
    assert "print is lowered for str, int, float, bool, and bytes values" in fence["message"]


def test_print_star_args_and_double_star_fence() -> None:
    starred = lower_source(
        """
args: list[int] = [1, 2]
print(*args)
"""
    )
    assert starred["fences"][0]["code"] == "PS2002"
    assert "starred calls are not lowered" in starred["fences"][0]["message"]
    splatted = lower_source("print(**{})\n")
    assert splatted["fences"][0]["code"] == "PS2002"
    assert "**kwargs are not lowered" in splatted["fences"][0]["message"]


def test_print_in_entry_loop_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "loop.py",
        "\n".join(
            [
                "def square(n: int) -> int:",
                "    return n * n",
                "",
                "",
                "for i in range(4):",
                "    print(i, square(i))",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "loop"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "0 0\n1 1\n2 4\n3 9\n"


def test_print_matches_cpython_oracle(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "kitchen.py",
        "\n".join(
            [
                "def label(name: str) -> str:",
                "    return name",
                "",
                "",
                'print(label("ada"), 1843)',
                "print(True, False, 0, -1)",
                "print(1000000, 0.001)",
                "print()",
                'print("end")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path)
