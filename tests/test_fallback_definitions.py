"""Tests for the per-definition fallback planner (issue #612).

Under `--fallback=cpython`, definitions whose lowering fences are planned for
the embedded CPython island and invoked through `ps_island_fn_invoke`; typed
siblings lower native. Mixed scripts stay byte-identical to CPython.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FALLBACK_FLAGS = ("--fallback=cpython",)


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _compile_and_compare(
    tmp_path: Path, source_text: str, name: str = "prog"
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    """Compile with the fallback tier, run, and diff against CPython."""
    src = tmp_path / f"{name}.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / f"{name}_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    return native, cpython


def test_fallback_mixed_planes_byte_identical(tmp_path: Path) -> None:
    """Unannotated definitions ride the island; typed siblings lower native."""
    source_text = """import json
from collections import defaultdict


def tally(rows):
    stats = defaultdict(int)
    for row in rows:
        stats[row["k"]] += row["v"]
    return dict(stats)


def scale(x, factor):
    return x * factor


def render(stats) -> str:
    parts = []
    for key in sorted(stats):
        parts.append(f"{key}={stats[key]}")
    return ",".join(parts)


def main() -> None:
    rows = json.loads('[{"k": "a", "v": 2}, {"k": "b", "v": 3}, {"k": "a", "v": 1}]')
    stats = tally(rows)
    print(render(stats))
    doubled: int = scale(21, 2)
    print(doubled)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text)
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "a=3,b=3\n42\n"


def test_fallback_island_alias_flag(tmp_path: Path) -> None:
    """`--island` is an alias for `--fallback=cpython` (Decision C)."""
    src = tmp_path / "alias.py"
    src.write_text(
        """def helper(x):
    return x + 1


def main() -> None:
    out: int = helper(41)
    print(out)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    for flags in (("--fallback=cpython",), ("--island",)):
        binary = tmp_path / "alias_bin"
        comp = _run_cli("compile", str(src), *flags, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
        assert comp.returncode == 0, comp.stderr
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
        assert run.returncode == 0, run.stderr
        assert run.stdout == "42\n"
        binary.unlink()


def test_fallback_native_caller_island_callee(tmp_path: Path) -> None:
    """A native function calls an island function and unmarshals the result."""
    source_text = """def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main() -> None:
    total: int = fib(10)
    label: str = f"fib(10)={total}"
    print(label)
    big: int = fib(20)
    print(big)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text)
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "fib(10)=55\n6765\n"


def test_fallback_island_caller_sibling_cascade(tmp_path: Path) -> None:
    """An island function calling a native sibling demotes the sibling transitively (issue #620)."""
    src = tmp_path / "sibling.py"
    src.write_text(
        """def leaf(x: int) -> int:
    return x + 10


def native_helper(x: int) -> int:
    return leaf(x) * 2


def island_fn(x):
    return native_helper(x)


def main() -> None:
    out: int = island_fn(3)
    print(out)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    dbg = tmp_path / "dbg"
    bin_path = tmp_path / "sib"
    comp = _run_cli(
        "compile",
        str(src),
        *FALLBACK_FLAGS,
        "--emit",
        "bin",
        "-o",
        str(bin_path),
        "--debug-dir",
        str(dbg),
        cwd=tmp_path,
    )
    assert comp.returncode == 0, comp.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    fb = summary["fallback"]
    assert fb["functions"]["island_fn"] == "island"
    assert fb["functions"]["native_helper"] == "island"
    assert fb["functions"]["leaf"] == "island"
    assert fb["demotedBy"]["native_helper"] == "island_fn"
    assert fb["demotedBy"]["leaf"] == "native_helper"

    # Compiled run matches CPython byte-identically
    run_cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    run_bin = subprocess.run([str(bin_path)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert run_bin.returncode == 0, run_bin.stderr
    assert run_bin.stdout == run_cpython.stdout
    assert run_bin.stdout == "26\n"


def test_fallback_island_caller_undemotable_sibling_fences(tmp_path: Path) -> None:
    """An island function calling an undemotable compiled sibling fences with PS2002 and says why (issue #620)."""
    src = tmp_path / "undemotable.py"
    src.write_text(
        """from typing import TypeVar

T = TypeVar("T")


def generic_helper(x: T) -> T:
    return x


def island_fn(x):
    y = [x, "mixed"]
    return generic_helper(x)


def main() -> None:
    out: int = island_fn(3)
    print(out)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(tmp_path / "sib"), cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS2002" in comp.stderr
    assert "island_fn runs on the island and cannot call the compiled function generic_helper" in comp.stderr
    assert "generic compiled function generic_helper cannot be demoted to the island" in comp.stderr
    assert "--emit binding" in comp.stderr


def test_fallback_plane_map_shows_demoted_by(tmp_path: Path) -> None:
    """Plane map in run --report and assess shows demotedBy (issue #620)."""
    src = tmp_path / "report_cascade.py"
    src.write_text(
        """def helper(x: int) -> int:
    return x * 2


def island_fn(x):
    y = [x, "mixed"]
    return helper(x)


def main() -> None:
    out: int = island_fn(5)
    print(out)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    from pyscriptc import assess

    assess_rep = assess.assess(src)
    assert assess_rep["planeMap"]["functions"]["helper"] == "island"
    assert assess_rep["planeMap"]["demotedBy"]["helper"] == "island_fn"

    text_rep = assess.render_text(assess_rep)
    assert "helper: island (demoted by island_fn)" in text_rep

    rep_file = tmp_path / "run_rep.json"
    run_proc = _run_cli("run", "--fallback=cpython", f"--report={rep_file}", str(src), cwd=tmp_path)
    assert run_proc.returncode == 0, run_proc.stderr
    assert run_proc.stdout == "10\n"
    run_data = json.loads(rep_file.read_text(encoding="utf-8"))
    assert run_data["planeMap"]["demotedBy"]["helper"] == "island_fn"


def test_fallback_top_level_fence_runs_island(tmp_path: Path) -> None:
    """A fenced module top level runs island-side; the output matches CPython."""
    src = tmp_path / "toplevel.py"
    src.write_text(
        """import os


def helper(x):
    return x + 1


name = os.environ["HOME"]


if __name__ == "__main__":
    print(helper(1))
""",
        encoding="utf-8",
    )
    native, cpython = _compile_and_compare(tmp_path, src.read_text(encoding="utf-8"), name="toplevel")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "2\n"


def test_fallback_island_top_level_main_guard_fires_once(tmp_path: Path) -> None:
    """The guard body runs island-side exactly once; the else branch never does."""
    source_text = """import json

CFG = json.loads('{"env": "prod"}')


def render(cfg) -> str:
    return "env=" + cfg["env"]


def main() -> str:
    return render(CFG)


if __name__ == "__main__":
    print(main())
else:
    print("else-ran")
"""
    native, cpython = _compile_and_compare(tmp_path, source_text)
    assert native.returncode == 0, native.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "env=prod\n"


def test_fallback_island_top_level_systemexit_status(tmp_path: Path) -> None:
    """`raise SystemExit(3)` out of an island top level sets the process status."""
    source_text = """STATUS = 3

if __name__ == "__main__":
    raise SystemExit(STATUS)
"""
    src = tmp_path / "exiter.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / "exiter_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == cpython.returncode == 3
    assert native.stdout == cpython.stdout == ""


def test_fallback_island_top_level_unbox_once_global(tmp_path: Path) -> None:
    """An annotated scalar global unboxes once; a typed native reader sees it."""
    source_text = """import os

GREETING: str = os.environ.get("GREETING", "top")


def greet(name: str) -> str:
    return GREETING + " " + name


print(greet("world"))
"""
    src = tmp_path / "unbox.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / "unbox_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    env = {**os.environ, "GREETING": "deep"}
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env
    )
    assert native.returncode == 0, native.stderr
    assert native.stdout == cpython.stdout == "deep world\n"


def test_fallback_island_top_level_native_write_fences(tmp_path: Path) -> None:
    """A native write to an island-owned global fences, naming the global."""
    source_text = """import os

COUNT = int(os.environ.get("COUNT", "1"))


def bump() -> int:
    global COUNT
    COUNT = COUNT + 1
    return COUNT


print(bump())
"""
    src = tmp_path / "writer.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / "writer_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    env = {**os.environ, "COUNT": "5"}
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env)
    cpython = subprocess.run(
        [sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env
    )
    assert native.returncode == cpython.returncode == 0
    assert native.stdout == cpython.stdout == "6\n"


def test_fallback_island_top_level_argv_and_file(tmp_path: Path) -> None:
    """`sys.argv` and `__file__` resolve inside an island top level."""
    source_text = """import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) != 2:
        print("usage")
        return 2
    print(sys.argv[1] + ":" + HERE.name)
    return 0


raise SystemExit(main())
"""
    src = tmp_path / "argvy.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / "argvy_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    native = subprocess.run([str(binary), "beta"], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    assert native.stdout == f"beta:{tmp_path.name}\n"
    bare = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert bare.returncode == 2
    assert bare.stdout == "usage\n"


def test_fallback_without_flag_still_refuses(tmp_path: Path) -> None:
    """Without the fallback flag, an unlowerable body fences as before."""
    src = tmp_path / "plain.py"
    src.write_text(
        """import json


def loader(text):
    return json.loads(text)


def main() -> None:
    rows: list[dict[str, int]] = loader('[{"a": 1}]')
    print(rows[0]["a"])


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(tmp_path / "plain"), cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS" in comp.stderr


def test_fallback_island_exception_crosses_seam(tmp_path: Path) -> None:
    """An island exception reaches native `except` through the type table."""
    source_text = """def risky(n):
    if n > 5:
        raise ValueError(f"too big: {n}")
    return n


def main() -> None:
    try:
        risky(10)
    except ValueError as exc:
        print(f"caught {exc}")
    else:
        print("no raise")


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text)
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "caught too big: 10\n"


def test_fallback_systemexit_status_from_island(tmp_path: Path) -> None:
    """`raise SystemExit(main())` exits with main's value (Decision E, #603)."""
    source_text = """def main() -> int:
    return 7


try:
    raise SystemExit(main())
except SystemExit:
    raise
"""
    native, cpython = _compile_and_compare(tmp_path, source_text)
    assert native.returncode == cpython.returncode
    assert native.returncode == 7


def test_fallback_checker_island_suppressed_passes_through(tmp_path: Path) -> None:
    """An island function with a checker error passes through under fallback (Refs #505)."""
    src = tmp_path / "island_err.py"
    src.write_text(
        """def untyped_helper(x):
    y: int = "suppressed"
    return x


def typed_fn(x: int) -> int:
    return int("123", base=10)


def main() -> None:
    out: int = untyped_helper(10)
    print(out + typed_fn(1))


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    # Plain compile without fallback must fail with checker exit code 2
    plain = _run_cli("compile", str(src), "--emit", "bin", "-o", str(tmp_path / "plain_bin"), cwd=tmp_path)
    assert plain.returncode == 2
    assert "bad-assignment" in plain.stderr

    # Under fallback, the diagnostic is suppressed as a note and binary runs successfully
    binary = tmp_path / "island_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    assert "note: suppressed 1 checker diagnostic routed to the island" in comp.stderr
    assert "bad-assignment" in comp.stderr

    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "133\n"


def test_fallback_checker_native_error_still_fails(tmp_path: Path) -> None:
    """A real type error inside a native definition still stops the compile with exit 2 (Refs #505)."""
    src = tmp_path / "native_err.py"
    src.write_text(
        """def untyped_helper(x):
    y: int = "suppressed"
    return x


def typed_fn(x: int) -> int:
    return int("123", base="bad")


def main() -> None:
    print(typed_fn(1))


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli(
        "compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(tmp_path / "native_bin"), cwd=tmp_path
    )
    assert comp.returncode == 2
    assert "note: suppressed 1 checker diagnostic routed to the island" in comp.stderr
    assert "int.__new__" in comp.stderr


def test_fallback_checker_sibling_package_import(tmp_path: Path) -> None:
    """Missing-import diagnostics for local sibling packages resolve via program dir (Refs #505)."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    sib_dir = app_dir / "sibling"
    sib_dir.mkdir()
    (sib_dir / "__init__.py").write_text("", encoding="utf-8")
    (sib_dir / "helper.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")

    src = app_dir / "main.py"
    src.write_text(
        """from sibling.helper import answer


def helper(x):
    return answer() + x


def main() -> None:
    val: int = helper(0)
    print(val)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    binary = app_dir / "main_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=app_dir)
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=app_dir)
    assert run.returncode == 0, run.stderr
    assert run.stdout == "42\n"


def test_fallback_optional_return_fall_off_end_plans_island(tmp_path: Path) -> None:
    """A function falling off the end of an Optional return probes island (issue #621)."""
    src = tmp_path / "optional_fall.py"
    src.write_text(
        """def parse_row(line: str) -> dict[str, str] | None:
    if "=" in line:
        parts = line.split("=")
        return {parts[0]: parts[1]}


def main() -> None:
    res1 = parse_row("a=1")
    print(res1)
    res2 = parse_row("comment")
    print(res2)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    dbg = tmp_path / "dbg"
    bin_path = tmp_path / "opt_bin"
    comp = _run_cli(
        "compile",
        str(src),
        *FALLBACK_FLAGS,
        "--emit",
        "bin",
        "-o",
        str(bin_path),
        "--debug-dir",
        str(dbg),
        cwd=tmp_path,
    )
    assert comp.returncode == 0, comp.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert summary["fallback"]["functions"]["parse_row"] == "island"

    # Compiled run matches CPython
    run_cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    run_bin = subprocess.run([str(bin_path)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert run_bin.returncode == 0, run_bin.stderr
    assert run_bin.stdout == run_cpython.stdout


def test_fallback_island_class_plane_map(tmp_path: Path) -> None:
    """A decorated class plans island, a plain class stays native (issue #628)."""
    src = tmp_path / "planes.py"
    src.write_text(
        """import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


class Pair:
    left: int
    right: int

    def __init__(self, left: int, right: int) -> None:
        self.left = left
        self.right = right

    def total(self) -> int:
        return self.left + self.right


def main() -> None:
    print(Pair(1, 2).total())


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    dbg = tmp_path / "dbg_classes"
    comp = _run_cli(
        "compile",
        str(src),
        *FALLBACK_FLAGS,
        "--emit",
        "ir",
        "-o",
        str(tmp_path / "planes.ir.json"),
        "--debug-dir",
        str(dbg),
        cwd=tmp_path,
    )
    assert comp.returncode == 0, comp.stderr
    summary = json.loads((dbg / "summary.json").read_text(encoding="utf-8"))
    assert summary["fallback"]["classes"]["Point"] == "island"
    assert summary["fallback"]["classes"]["Pair"] == "native"
    module = json.loads((tmp_path / "planes.ir.json").read_text(encoding="utf-8"))
    assert "@dataclasses.dataclass(frozen=True, slots=True)" in module["islandSource"]


def test_fallback_island_class_total_ordering(tmp_path: Path) -> None:
    """`@functools.total_ordering` falls to the island and runs as CPython (issue #628)."""
    source_text = """import functools


@functools.total_ordering
class Version:
    def __init__(self, text: str, weight: int) -> None:
        self.text = text
        self.weight = weight

    def __lt__(self, other: Version) -> bool:
        return self.weight < other.weight


def main() -> None:
    older = Version("alpha", 2)
    newer = Version("beta", 1)
    print(older.text)
    print(newer < older)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="total_ordering")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "alpha\nTrue\n"


def test_fallback_island_class_dataclass_unusual_args(tmp_path: Path) -> None:
    """A dataclass with arguments the native form refuses runs island-side (issue #628)."""
    source_text = """import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


def main() -> None:
    p = Point(10, 20)
    print(p.x, p.y)
    print(p)
    p.x = 5


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="frozen_dc")
    # The frozen write raises CPython's FrozenInstanceError on the island.
    assert cpython.returncode != 0
    assert native.returncode != 0
    assert native.stdout == cpython.stdout == "10 20\nPoint(x=10, y=20)\n"


def test_fallback_island_class_method_body_fence(tmp_path: Path) -> None:
    """A class that only fences while its methods lower joins the island (issue #628)."""
    source_text = """class Widget:
    size: int

    def __init__(self) -> None:
        self.size = 2

    def label(self) -> str:
        parts = map(str, [1, 2])
        return ",".join(parts)


def main() -> None:
    w = Widget()
    print(w.label())


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="body_fence")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "1,2\n"


def test_fallback_island_class_local_decorator(tmp_path: Path) -> None:
    """A class decorated by a local untyped decorator rides the island (issue #628)."""
    source_text = """def tagged(cls):
    cls.kind = "tagged"
    return cls


@tagged
class Marker:
    def __init__(self, name: str) -> None:
        self.name = name


def main() -> None:
    marker = Marker("first")
    print(marker.name)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="local_deco")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "first\n"


def test_fallback_island_class_attrs(tmp_path: Path) -> None:
    """`@attrs.define` (the issue's repro) runs when attrs is installed (issue #628)."""
    import pytest

    pytest.importorskip("attrs")
    source_text = """import attrs


@attrs.define
class Point:
    x: int
    y: int


def main() -> None:
    p = Point(10, 20)
    print(p.x, p.y)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="attrs_class")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "10 20\n"


def test_fallback_native_class_uses_island_class_handle(tmp_path: Path) -> None:
    """A native class method reaches the island class through its handle (issue #628)."""
    source_text = """import dataclasses


@dataclasses.dataclass(frozen=True)
class Point:
    x: int
    y: int


class Screen:
    label: str

    def __init__(self) -> None:
        p: Point = Point(3, 4)
        x: int = p.x
        y: int = p.y
        self.label = f"{x},{y}"


def main() -> None:
    screen = Screen()
    print(screen.label)


if __name__ == "__main__":
    main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, name="class_handle")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "3,4\n"


def test_fallback_module_import_fence_ps2001_runs_island(tmp_path: Path) -> None:
    """Module-level unlowered stdlib import (PS2001) runs module top level on island (issue #627)."""
    from pyscriptc import assess

    source_text = """from datetime import datetime, timezone


def main() -> None:
    print(timezone.utc)


if __name__ == "__main__":
    main()
"""
    src = tmp_path / "ps2001_top.py"
    src.write_text(source_text, encoding="utf-8")
    assess_report = assess.assess(src)
    assert assess_report["planeMap"]["moduleTopLevel"] == "island"

    native, cpython = _compile_and_compare(tmp_path, source_text, name="ps2001_top")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    assert native.stdout == "UTC\n"


def test_fallback_module_import_fence_ps2002_relative_runs_island(tmp_path: Path) -> None:
    """Module-level relative import (PS2002) runs module top level on island (issue #627)."""
    from pyscriptc import assess

    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "compress.py").write_text("x = 42\n", encoding="utf-8")
    src = pkg_dir / "runme.py"
    src.write_text(
        """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mypkg

__package__ = "mypkg"
from .compress import x


def main() -> None:
    print(x)


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    assess_report = assess.assess(src)
    assert assess_report["planeMap"]["moduleTopLevel"] == "island"

    binary = tmp_path / "ps2002_bin"
    comp = _run_cli("compile", str(src), *FALLBACK_FLAGS, "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "42\n"


def test_fallback_seam_list_mutation_copyback(tmp_path: Path) -> None:
    """A native caller sees an island callee's in-place list mutation (issue #674).

    `bump` fences (subscript augmented assignment), so `main` lowers native
    and passes its list through the seam: before the fix the island mutated
    a marshalled copy and `main` printed the first sum twice.
    """
    source_text = """def bump(xs: list[float], k: float) -> None:
    for i in range(len(xs)):
        xs[i] -= k


def show(xs: list[float]) -> float:
    return xs[0] + xs[1]


def main() -> None:
    xs = [1.0, 2.0]
    print(f"{show(xs):.3f}")
    for _ in range(3):
        bump(xs, 0.5)
    print(f"{show(xs):.3f}")


main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, "seam674")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout == "3.000\n0.000\n"


def test_fallback_seam_nbody_energy_matches_cpython(tmp_path: Path) -> None:
    """An nbody-like native caller whose island callee advances the bodies (issue #674)."""
    source_text = """def advance(bodies: list[float], dt: float) -> None:
    for i in range(0, len(bodies), 4):
        bodies[i] += dt * bodies[i + 2]
        bodies[i + 1] += dt * bodies[i + 3]


def energy(bodies: list[float]) -> float:
    e = 0.0
    for i in range(0, len(bodies), 4):
        e += 0.5 * bodies[i + 2] * bodies[i + 2]
        e += 0.5 * bodies[i + 3] * bodies[i + 3]
    return e


def main() -> None:
    bodies = [1.0, 0.0, 0.0, 0.5]
    print(f"{energy(bodies):.6f}")
    for _ in range(10):
        advance(bodies, 0.01)
    print(f"{energy(bodies):.6f}")


main()
"""
    native, cpython = _compile_and_compare(tmp_path, source_text, "nbody674")
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
