"""Gates: imports, containers, classes, and the first queued stdlib module."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

QUEUE = (
    "math",
    "re",
    "json",
    "urllib.parse",
    "base64",
    "hashlib",
    "struct",
    "datetime",
    "time",
    "collections",
    "functools",
    "itertools",
    "operator",
    "contextlib",
    "os",
    "posixpath",
    "pathlib",
    "stat",
    "fnmatch",
    "warnings",
    "io",
    "sys",
    "importlib",
    "inspect",
    "threading",
    "logging",
    "asyncio",
    "subprocess",
    "socket",
    "ssl",
    "ctypes",
    "string",
    "bisect",
    "errno",
    "importlib.resources",
    "hmac",
    "unicodedata",
    "tempfile",
    "atexit",
    "shutil",
    "enum",
    "dataclasses",
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="latin-1",
    )


def oracle(path: Path, func: str, args: str = "[]") -> str:
    source = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"value = mod.{func}(*{args})\n"
        "print('%.17g' % value if isinstance(value, float) else value)\n"
    )
    proc = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True, check=True)
    return proc.stdout


def compile_run(tmp_path: Path, source: Path, func: str, args: str = "[]") -> subprocess.CompletedProcess[str]:
    return run_cli(
        "compile",
        str(source),
        "--run",
        "--call",
        func,
        "--args",
        args,
        "-o",
        str(tmp_path / func),
    )


def write_case(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_stdlib_queue_is_locked() -> None:
    from pyscriptc.stdlib_queue import LOWERED, module_status
    from pyscriptc.stdlib_queue import QUEUE as queued

    assert queued == QUEUE
    assert LOWERED == frozenset(QUEUE)
    assert module_status("math") == "lowered"
    assert module_status("json") == "lowered"
    assert module_status("urllib") == "lowered"
    assert module_status("urllib.parse") == "lowered"
    assert module_status("urllib.request") is None
    assert module_status("datetime") == "lowered"
    assert module_status("socket") == "lowered"
    assert module_status("pickle") is None
    assert module_status("typing") is None


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_gate_examples_match_cpython(tmp_path: Path) -> None:
    cases = [
        (EXAMPLES / "gates_math.py", "hypotenuse", "[3.0, 4.0]"),
        (EXAMPLES / "gates_math.py", "floors", "[]"),
        (EXAMPLES / "gates_list.py", "baked", "[]"),
        (EXAMPLES / "gates_list.py", "countdown", "[]"),
        (EXAMPLES / "gates_dict.py", "lookup", "[]"),
        (EXAMPLES / "gates_list.py", "splice", "[]"),
        (EXAMPLES / "gates_list.py", "delidx", "[]"),
        (EXAMPLES / "gates_set.py", "algebra", "[]"),
        (EXAMPLES / "gates_set.py", "methods", "[]"),
        (EXAMPLES / "rest_probe.py", "iostream", "[]"),
        (EXAMPLES / "gates_try.py", "clamp", "[-3]"),
        (EXAMPLES / "gates_try.py", "clamp", "[4]"),
        (EXAMPLES / "gates_class.py", "origin_sum", "[]"),
        (EXAMPLES / "gates_str.py", "tidy", '["  HI  "]'),
        (EXAMPLES / "gates_str.py", "starts", '["abc"]'),
        (EXAMPLES / "gates_str.py", "starts", '["zz"]'),
        (EXAMPLES / "gates_probe.py", "probe", "[]"),
    ]
    for path, func, args in cases:
        got = compile_run(tmp_path, path, func, args)
        assert got.returncode == 0, f"{func} {args}\n{got.stderr}"
        assert got.stdout == oracle(path, func, args)


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_nan_membership_uses_equality(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nan_in.py",
        "\n".join(
            [
                "import math",
                "def found() -> int:",
                "    nans: list[float] = [math.nan]",
                "    if math.nan in nans:",
                "        return 1",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "found")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_input_reads_a_line(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "echo.py",
        "def echo() -> str:\n    s: str = input()\n    return s.upper()\n",
    )
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--call",
            "echo",
            "-o",
            str(tmp_path / "echo"),
        ],
        cwd=ROOT,
        env=ENV,
        input="quim\n",
        capture_output=True,
        text=True,
    )
    assert got.returncode == 0, got.stderr
    oracle_src = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(mod.echo())\n"
    )
    want = subprocess.run(
        [sys.executable, "-c", oracle_src],
        cwd=ROOT,
        input="quim\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert got.stdout == want.stdout


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_empty_for_target_reads_as_zero(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "zero.py",
        "def after_empty() -> int:\n    for i in range(0):\n        pass\n    return i\n",
    )
    got = compile_run(tmp_path, path, "after_empty")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_forward_class_field(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "forward.py",
        "\n".join(
            [
                "class Holder:",
                "    item: Box",
                "    def __init__(self, item: Box) -> None:",
                "        self.item = item",
                "class Box:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "def read() -> int:",
                "    box: Box = Box(6)",
                "    holder: Holder = Holder(box)",
                "    return holder.item.n",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "read")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "read")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_exceptions(tmp_path: Path) -> None:
    cases = [
        (
            "step.py",
            (
                "def f() -> int:\n"
                "    total: int = 0\n"
                "    for i in range(0, 1, 0):\n"
                "        total = total + i\n"
                "    return total\n"
            ),
            "ValueError",
        ),
        (
            "key.py",
            'def f() -> int:\n    table: dict[str, int] = {"a": 1}\n    return table["b"]\n',
            "KeyError",
        ),
        (
            "index.py",
            "def f() -> int:\n    xs: list[int] = [1]\n    return xs[3]\n",
            "IndexError",
        ),
        (
            "sqrt.py",
            "import math\ndef f() -> float:\n    return math.sqrt(-1.0)\n",
            "ValueError",
        ),
        (
            "floor.py",
            "import math\ndef f() -> int:\n    return math.floor(1e20)\n",
            "OverflowError",
        ),
        (
            "nan.py",
            "import math\ndef f() -> int:\n    return int(math.nan)\n",
            "ValueError",
        ),
        (
            "sin.py",
            "import math\ndef f() -> float:\n    return math.sin(math.inf)\n",
            "ValueError",
        ),
        (
            "pow.py",
            "import math\ndef f() -> float:\n    return math.pow(0.0, -1.0)\n",
            "ValueError",
        ),
        (
            "rethrow.py",
            (
                "def f() -> int:\n"
                "    try:\n"
                '        raise RuntimeError("nope")\n'
                "    except ValueError:\n"
                "        return 0\n"
                "    return 1\n"
            ),
            "RuntimeError",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 1, f"{name}\ncode {got.returncode}\n{got.stderr}"
        assert text in got.stderr


def test_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        ("pickle.py", "import pickle\n\ndef f() -> int:\n    return 1\n", "PS2002", "pickle"),
        ("gcd.py", "from math import gcd\n\ndef f() -> int:\n    return gcd(6, 9)\n", "PS2001", "math.gcd"),
        (
            "bools.py",
            "def f() -> int:\n    xs: list[bool] = [True]\n    return 1\n",
            "PS2002",
            "list[bool]",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr


def test_bool_is_not_promoted_to_float(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "flag.py",
        "import math\n\ndef f(flag: bool) -> float:\n    return math.sqrt(flag)\n",
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "flag.ll"))
    assert got.returncode == 3, got.stderr
    assert "PS1003" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_queued_import_without_use_still_lowers(tmp_path: Path) -> None:
    path = write_case(tmp_path, "urllib_only.py", "import urllib\n\ndef f() -> int:\n    return 1\n")
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "1\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_emit_bin_without_call_builds_a_noop_main(tmp_path: Path) -> None:
    path = write_case(tmp_path, "lib.py", "def f() -> int:\n    return 1\n")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / "lib"))
    assert got.returncode == 0, got.stderr
    ran = subprocess.run([str(tmp_path / "lib")], capture_output=True, text=True)
    assert ran.returncode == 0
    assert ran.stdout == ""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_unpack_assignment_forms(tmp_path: Path) -> None:
    cases = [
        (
            "tuple_rhs.py",
            'def f() -> int:\n    pair: tuple[int, str] = (7, "x")\n    a, b = pair\n    return a + len(b)\n',
        ),
        (
            "list_rhs.py",
            "def f() -> int:\n    xs: list[int] = [3, 4]\n    a, b = xs\n    return a * 10 + b\n",
        ),
        (
            "chained.py",
            "def side() -> int:\n    return 4\n\ndef f() -> int:\n    a = b = side()\n    return a + b\n",
        ),
        (
            "enumerate.py",
            'def f() -> int:\n    xs: list[str] = ["a", "bb"]\n    total: int = 0\n'
            "    for i, x in enumerate(xs):\n        total = total + i * 10 + len(x)\n    return total\n",
        ),
        (
            "items.py",
            'def f() -> int:\n    table: dict[str, int] = {"a": 1, "bb": 2}\n    total: int = 0\n'
            "    for k, v in table.items():\n        total = total + len(k) * 10 + v\n    return total\n",
        ),
        (
            "zip_pairs.py",
            'def f() -> int:\n    xs: list[int] = [1, 2]\n    ys: list[str] = ["a", "bb"]\n    total: int = 0\n'
            "    for a, b in zip(xs, ys):\n        total = total + a * 10 + len(b)\n    return total\n",
        ),
        (
            "for_pairs.py",
            "def f() -> int:\n    ps: list[tuple[int, int]] = [(1, 2), (3, 4)]\n    total: int = 0\n"
            "    for a, b in ps:\n        total = total + a * 10 + b\n    return total\n",
        ),
        (
            "star_head.py",
            "def f() -> int:\n    xs: list[int] = [1, 2, 3, 4]\n    first, *rest = xs\n"
            "    return first + rest[0] * 10 + len(rest)\n",
        ),
        (
            "star_mid.py",
            "def f() -> int:\n    xs: list[int] = [1, 2, 3, 4]\n    a, *rest, z = xs\n"
            "    return a + z * 10 + len(rest)\n",
        ),
        (
            "nested.py",
            "def f() -> int:\n    pair: tuple[int, tuple[int, int]] = (1, (2, 3))\n"
            "    a, (b, c) = pair\n    return a * 100 + b * 10 + c\n",
        ),
        (
            "list_target.py",
            "def f() -> int:\n    xs: list[int] = [3, 4]\n    [a, b] = xs\n    return a * b\n",
        ),
        (
            "module_consts.py",
            'A, B = (1, "s")\nC = D = 9\n\ndef f() -> int:\n    return A + len(B) + C + D\n',
        ),
        (
            "comp_target.py",
            "def f() -> int:\n    ps: list[tuple[int, int]] = [(1, 2), (3, 4)]\n"
            "    xs: list[int] = [a + b for a, b in ps]\n    return xs[0] * 10 + xs[1]\n",
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == oracle(path, "f"), name


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_unpack_wrong_arity_raises(tmp_path: Path) -> None:
    cases = [
        (
            "few.py",
            "def f() -> int:\n    xs: list[int] = [1]\n    a, b = xs\n    return a + b\n",
            "not enough values to unpack (expected 2, got 1)",
        ),
        (
            "many.py",
            "def f() -> int:\n    xs: list[int] = [1, 2, 3]\n    a, b = xs\n    return a + b\n",
            "too many values to unpack (expected 2)",
        ),
        (
            "star.py",
            "def f() -> int:\n    xs: list[int] = []\n    a, *rest = xs\n    return a\n",
            "not enough values to unpack (expected at least 1, got 0)",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 1, f"{name}\ncode {got.returncode}\n{got.stderr}"
        assert text in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_for_unpack_wrong_arity_raises(tmp_path: Path) -> None:
    # A wrong for-target count binds the names and raises the CPython
    # ValueError per iteration, so an empty iterable never raises.
    full = write_case(
        tmp_path,
        "full.py",
        "def f() -> int:\n    ps: list[tuple[int, int]] = [(1, 2)]\n    total: int = 0\n"
        "    for a, b, c in ps:\n        total = total + a\n    return total\n",
    )
    got = run_cli("compile", str(full), "--skip-check", "--run", "--call", "f", "-o", str(tmp_path / "full"))
    assert got.returncode == 1, got.stderr
    assert "not enough values to unpack (expected 3, got 2)" in got.stderr
    empty = write_case(
        tmp_path,
        "empty.py",
        "def f() -> int:\n    ps: list[tuple[int, int]] = []\n    total: int = 7\n"
        "    for a, b, c in ps:\n        total = 0\n    return total\n",
    )
    got = run_cli("compile", str(empty), "--skip-check", "--run", "--call", "f", "-o", str(tmp_path / "empty"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"


def test_unpack_fences_stay(tmp_path: Path) -> None:
    # A static tuple arity mismatch and the non-name/non-tuple shapes fence;
    # only a dynamic-length (list) RHS raises the CPython ValueError at run time.
    cases = [
        (
            "arity.py",
            "def f() -> int:\n    pair: tuple[int, int] = (1, 2)\n    a, b, c = pair\n    return a\n",
            "needs 2 names",
        ),
        (
            "star_arity.py",
            "def f() -> int:\n    pair: tuple[int, int] = (1, 2)\n    a, b, c, *rest = pair\n    return a\n",
            "starred unpack",
        ),
        (
            "str_rhs.py",
            'def f() -> int:\n    a, b = "xy"\n    return 1\n',
            "tuple or list",
        ),
        (
            "nested_for.py",
            "def f() -> int:\n    ps: list[tuple[int, tuple[int, int]]] = [(1, (2, 3))]\n"
            "    for a, (b, c) in ps:\n        pass\n    return 1\n",
            "flat names",
        ),
        (
            "range_unpack.py",
            "def f() -> int:\n    for a, b in range(3):\n        pass\n    return 1\n",
            "enumerate/zip",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS2002" in got.stderr
        assert text in got.stderr
