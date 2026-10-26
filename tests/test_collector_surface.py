"""The collector surface: os.walk, json.load and time.perf_counter (#509).

Each lowered form diffs stdout against the CPython 3.14 oracle over the same
tree; the fenced forms keep their PS code. The walk rows and the visit order
follow CPython's topdown walk over the readdir order both runtimes read, and
the tree is built by the test so the two runs see the same bytes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, run_cli, write_case


def _tree(tmp_path: Path) -> Path:
    """A fixed tree: two files at the root, nested directories, one file deep."""
    (tmp_path / "src" / "b" / "c").mkdir(parents=True)
    (tmp_path / "src" / "d").mkdir()
    (tmp_path / "src" / "root.txt").write_text("r", encoding="utf-8")
    (tmp_path / "src" / "b" / "mid.txt").write_text("m", encoding="utf-8")
    (tmp_path / "src" / "b" / "c" / "deep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "d" / "leaf.txt").write_text("l", encoding="utf-8")
    return tmp_path / "src"


def _oracle_run(tmp_path: Path, name: str, source: str, args: str = "[]") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run", args=args)
    assert got.returncode == 0, got.stderr
    want = oracle(path, "run", args)
    assert got.stdout == want, f"compiled:\n{got.stdout}\ncpython:\n{want}"


def test_os_walk_matches_cpython(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _oracle_run(
        tmp_path,
        "walk.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                f"    for d, dirs, files in os.walk({str(root)!r}):",
                "        print(d)",
                "        for name in dirs:",
                "            print('d', name)",
                "        for name in files:",
                "            print('f', name)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_walk_counts_and_visit_order(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    _oracle_run(
        tmp_path,
        "walkcount.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    total = 0",
                "    visits = 0",
                f"    for d, dirs, files in os.walk({str(root)!r}):",
                "        total += len(dirs) + len(files)",
                "        visits += 1",
                "    print(visits)",
                "    print(total)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_walk_relative_and_missing(tmp_path: Path) -> None:
    _tree(tmp_path)
    _oracle_run(
        tmp_path,
        "walkrel.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    for d, dirs, files in os.walk('src'):",
                "        print(d)",
                "    return 0",
                "",
            ]
        ),
    )
    # A missing top yields nothing, like CPython's swallowed scandir error.
    _oracle_run(
        tmp_path,
        "walkmiss.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    seen = 0",
                "    for d, dirs, files in os.walk('no-such-dir'):",
                "        seen += 1",
                "    print(seen)",
                "    return 0",
                "",
            ]
        ),
    )


def test_time_perf_counter_matches_cpython(tmp_path: Path) -> None:
    _oracle_run(
        tmp_path,
        "perf.py",
        "\n".join(
            [
                "import time",
                "",
                "def run() -> int:",
                "    start: float = time.perf_counter()",
                "    elapsed: float = time.perf_counter() - start",
                "    print(elapsed >= 0.0)",
                "    print(start > 0.0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_json_load_matches_cpython(tmp_path: Path) -> None:
    doc = tmp_path / "data.json"
    doc.write_text('{"a": 1, "b": 5}', encoding="utf-8")
    _oracle_run(
        tmp_path,
        "jload.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                f"    with open({str(doc)!r}, 'r') as fp:",
                "        data: dict[str, int] = json.load(fp)",
                "    print(data['a'])",
                "    print(data['b'])",
                "    return 0",
                "",
            ]
        ),
    )
    # A list document rides the same annotation-driven loads path.
    doc2 = tmp_path / "list.json"
    doc2.write_text("[10, 20, 30]", encoding="utf-8")
    _oracle_run(
        tmp_path,
        "jloadlist.py",
        "\n".join(
            [
                "import json",
                "",
                "def run() -> int:",
                f"    with open({str(doc2)!r}, 'r') as fp:",
                "        xs: list[int] = json.load(fp)",
                "    total = 0",
                "    for x in xs:",
                "        total += x",
                "    print(total)",
                "    return 0",
                "",
            ]
        ),
    )


def test_collector_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        (
            "walkkw.py",
            "import os\n\ndef f(s: str) -> int:\n"
            "    for d, ds, fs in os.walk(s, topdown=False):\n        pass\n    return 1\n",
            "PS2003",
            "keyword arguments are not lowered",
        ),
        (
            "walkpath.py",
            "import os\n"
            "import pathlib\n"
            "\n"
            "def f(p: pathlib.Path) -> int:\n"
            "    for d, ds, fs in os.walk(p):\n"
            "        pass\n"
            "    return 1\n",
            "PS1003",
            "os.walk top is str",
        ),
        (
            "jsondump.py",
            "import json\n\ndef f(d: dict[str, int], fp: str) -> int:\n"
            "    json.dump(d, fp)  # type: ignore[arg-type]\n    return 1\n",
            "PS2001",
            "json.dump is not in the lowered subset of json",
        ),
        (
            "jloadnoanno.py",
            "import json\n\ndef f(fp: str) -> int:\n    d = json.load(fp)  # type: ignore[arg-type]\n    return 1\n",
            "PS1001",
            "json.load needs the result annotation",
        ),
        (
            "jloadstr.py",
            "import json\n\ndef f(s: str) -> int:\n"
            "    d: dict[str, int] = json.load(s)  # type: ignore[arg-type]\n    return 1\n",
            "PS1003",
            "json.load argument is a file handle",
        ),
        (
            "perfns.py",
            "import time\n\ndef f() -> int:\n    return time.perf_counter_ns()  # type: ignore[attr-defined]\n",
            "PS2001",
            "time.perf_counter_ns is not in the lowered subset of time",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode != 0, f"{name} compiled:\n{got.stdout}"
        combined = got.stderr + got.stdout
        assert code in combined, f"{name}: {combined}"
        assert text in combined, f"{name}: {combined}"


def test_os_walk_island_unchanged(tmp_path: Path) -> None:
    """Under the fallback tier the definition still routes to the island and
    the observable output matches CPython, as it did before the walk lowered."""
    root = _tree(tmp_path)
    src = tmp_path / "prog.py"
    src.write_text(
        "import os\n"
        "\n"
        "\n"
        "def count(path: str) -> int:\n"
        "    n = 0\n"
        f"    for d, ds, fs in os.walk({str(root)!r}):\n"
        "        n += len(fs)\n"
        "    return n\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    print(count('.'))\n"
        "\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    binary = tmp_path / "bin"
    from pyscriptc.cli import main as cli_main

    assert (
        cli_main(["compile", str(src), "--fallback=cpython", "--emit", "bin", "--skip-check", "-o", str(binary)]) == 0
    )
    got = subprocess.run([str(binary)], capture_output=True, text=True, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, cwd=tmp_path)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout
