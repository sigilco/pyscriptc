"""The rest of the queue: clocks, paths, object helpers, and the island."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import EXAMPLES, compile_run, oracle, run_cli, write_case


def test_rest_probe_matches_cpython(tmp_path: Path) -> None:
    path = EXAMPLES / "rest_probe.py"
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout == "0\n"


def test_invalid_date_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "leap.py",
        "import datetime\n\ndef f() -> int:\n    return datetime.date(2019, 2, 29).day\n",
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1, got.stderr
    assert "ValueError" in got.stderr


def test_sys_exit_propagates(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "exit.py",
        "import sys\n\ndef f() -> int:\n    sys.exit(2)\n    return 0\n",
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 2, got.stderr


def test_benchmarks_do_not_link_the_island(tmp_path: Path) -> None:
    out = tmp_path / "sum.ll"
    got = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out))
    assert got.returncode == 0, got.stderr
    assert "ps_island_" not in out.read_text(encoding="utf-8")


def test_rest_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        ("pickle.py", "import pickle\n\ndef f() -> int:\n    return 1\n", "PS2002", "pickle"),
        (
            "run.py",
            "\n".join(
                [
                    "import asyncio",
                    "def f() -> int:",
                    "    asyncio.run(None)  # type: ignore[arg-type]",
                    "    return 1",
                    "",
                ]
            ),
            "PS2001",
            "coroutines are not lowered",
        ),
        (
            "loadmod.py",
            "import importlib\n\ndef f() -> int:\n    importlib.import_module('math')\n    return 1\n",
            "PS2001",
            "import_module returns a module",
        ),
        (
            "partial.py",
            "import functools\n\ndef f() -> int:\n    functools.partial(abs, 1)\n    return 1\n",
            "PS2001",
            "functools.partial",
        ),
        (
            "count.py",
            "import itertools\n\ndef f() -> int:\n    itertools.count()\n    return 1\n",
            "PS2001",
            "infinite iterators",
        ),
        (
            "item.py",
            "import operator\n\ndef f() -> int:\n    operator.itemgetter(0)\n    return 1\n",
            "PS2001",
            "operator.itemgetter",
        ),
        (
            "alias.py",
            "\n".join(
                [
                    "from contextlib import suppress",
                    "def f() -> int:",
                    "    with suppress(ValueError) as caught:",
                    "        return 1",
                    "    return 0",
                    "",
                ]
            ),
            "PS2002",
            "with-as",
        ),
        (
            "kw.py",
            "import hashlib\n\ndef f() -> int:\n    hashlib.sha256(b'ab', usedforsecurity=False)\n    return 1\n",
            "PS2003",
            "keyword arguments",
        ),
        (
            "plus.py",
            "def f() -> str:\n    return open('/tmp/pyscriptc-rest.txt', 'r+').read()\n",
            "PS2001",
            "open mode",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr
