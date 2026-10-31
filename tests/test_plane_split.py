"""The per-module plane split (the island's granularity is the module).

Under `--fallback=cpython` a program whose units are plain siblings keeps
both lanes: a fenced module rides its own island floor and the clean
modules lower native, with a cross-module call crossing the boundary
through the boxed seam (`ps_island_mod_fn_invoke`, one island namespace
per program module). Every case diffs the compiled binary against CPython.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _compile(tmp_path: Path, prog: Path, *flags: str) -> tuple[Path, subprocess.CompletedProcess[str]]:
    binary = tmp_path / "bin"
    comp = _cli(
        "compile",
        str(prog),
        "--program",
        "--fallback=cpython",
        "--emit",
        "bin",
        "-o",
        str(binary),
        *flags,
        cwd=tmp_path,
    )
    return binary, comp


def _compare(tmp_path: Path, prog: Path) -> str:
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    return got.stdout


def _plane_map(tmp_path: Path, prog: Path) -> dict:
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    report = json.loads((binary.parent / f"{binary.name}.report.json").read_text(encoding="utf-8"))
    return report["planeMap"]


def test_three_module_program_splits_the_planes(tmp_path: Path) -> None:
    """`clean.add` and `__main__` stay native; the fenced module rides the island.

    The three-module shape of the plane split: `clean.add` is fully
    annotated, `dirty.broken` is not, and the entry calls both. The
    program keeps the native lane for the two clean definitions and
    crosses the boundary for the fenced one.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "3\nX\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["clean.add"] == "native"
    assert planes["__main__.main"] == "native"
    assert planes["dirty.broken"] == "island"


def test_split_keeps_the_native_definitions_in_the_binary(tmp_path: Path) -> None:
    """The native lane is real: the binary carries `clean.add` as LLVM code."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    assert "define i64 @clean.add" in llvm
    assert "define void @__main__.main" in llvm
    # The boundary call is the boxed seam, namespaced by the defining module.
    assert "ps_island_mod_fn_invoke_value" in llvm
    assert "ps_island_mod_seed" in llvm


def test_two_fenced_modules_keep_their_own_namespaces(tmp_path: Path) -> None:
    """Two fenced modules with the same definition name stay apart.

    The island namespace is per module, so `one.work` and `two.work` are
    two different functions; the namespaced seam keeps them apart.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from one import work as work_one\n"
                "from two import work as work_two\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    first: str = work_one('a')\n"
                "    second: str = work_two('b')\n"
                "    print(first, second)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "one.py": "def work(s):\n    return s + '1'\n",
            "two.py": "def work(s):\n    return s + '2'\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "a1 b2\n"


def test_fenced_module_with_a_native_sibling(tmp_path: Path) -> None:
    """One module, one fenced definition and one clean one: the clean one stays native."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from mixed import clean_one, dirty_one\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    value: int = clean_one(3)\n"
                "    print(value)\n"
                "    text: str = dirty_one('y')\n"
                "    print(text)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "mixed.py": (
                "def clean_one(n: int) -> int:\n    return n + 1\n\n\ndef dirty_one(s):\n    return s.upper()\n"
            ),
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "4\nY\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["mixed.clean_one"] == "native"
    assert planes["mixed.dirty_one"] == "island"


def test_island_definition_calling_a_native_sibling_refuses(tmp_path: Path) -> None:
    """The island-to-compiled seam refuses across modules too.

    An island definition calling a native definition of another module
    has no seam to cross: the native definition exists only in the
    compiled lane. The refusal names the call, so the fix is visible.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    print(broken('x'))\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "from clean import add\n\n\ndef broken(s):\n    return s.upper() + str(add(1, 1))\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 3, comp.stdout
    assert "PS2002" in comp.stderr
    assert "clean.add" in comp.stderr
    assert "island-to-compiled seam" in comp.stderr


def test_package_shaped_program_still_runs_whole_island(tmp_path: Path) -> None:
    """A package program keeps the whole-program island.

    The per-module seed cannot reproduce the package semantics (the
    relative imports and the package `__init__` body), so a package
    program runs whole island-side, as before.
    """
    pkg = tmp_path / "pkg"
    _write(
        pkg,
        {
            "__init__.py": "print('init', __name__)\n",
            "__main__.py": "from pkg.cli import main\nmain()\n",
            "cli.py": (
                "from . import util\n"
                "from .util import shout\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(shout('hi'), util.where())\n"
            ),
            "util.py": (
                "import os\n"
                "\n"
                "\n"
                "def shout(s):\n"
                "    return s.upper()\n"
                "\n"
                "\n"
                "def where() -> str:\n"
                "    return os.path.basename(__file__)\n"
            ),
        },
    )
    binary = tmp_path / "bin"
    comp = _cli(
        "compile", str(pkg), "--program", "--fallback=cpython", "--emit", "bin", "-o", str(binary), cwd=tmp_path
    )
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run(
        [sys.executable, "-m", "pkg"], capture_output=True, text=True, timeout=60, cwd=str(pkg.parent)
    )
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout


def test_split_program_runs_away_from_the_source_tree(tmp_path: Path) -> None:
    """The split binary does not need the program directory at run time.

    The island namespace carries the fenced module's own definitions, so
    the binary does not import the program's modules from disk.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from dirty import broken\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    total: int = add(1, 2)\n"
                "    print(total)\n"
                "    upper: str = broken('x')\n"
                "    print(upper)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "dirty.py": "def broken(s):\n    return s.upper()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=elsewhere)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)


def test_island_definition_raising_propagates(tmp_path: Path) -> None:
    """An exception out of the island definition reaches the native caller."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from dirty import boom\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    try:\n"
                "        boom('boom')\n"
                "    except ValueError:\n"
                "        print('caught')\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "dirty.py": "def boom(s):\n    raise ValueError(s)\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout) == (want.returncode, want.stdout)
    assert got.stdout == "caught\n"


def _class_shapes(root: Path) -> None:
    _write(
        root,
        {
            "shapes.py": (
                "import dataclasses\n"
                "\n"
                "\n"
                "@dataclasses.dataclass(frozen=True, slots=True)\n"
                "class Point:\n"
                "    x: int\n"
                "    y: int\n"
            ),
        },
    )


def test_island_class_import_does_not_fence_the_consumer_top_level(tmp_path: Path) -> None:
    """`from shapes import Point` binds across the boundary instead of fencing.

    Before the export table carried island classes, this import fenced at
    module level and Decision A dragged the consumer's whole top level
    island-side. The consumer stays native; only the uses that need the
    class natively (a base class, an except clause) fence their own
    definition.
    """
    prog = tmp_path / "prog"
    _class_shapes(prog)
    _write(
        prog,
        {
            "__main__.py": (
                "from shapes import Point\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    p = Point(10, 20)\n"
                "    print(p)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "Point(x=10, y=20)\n"
    planes = _plane_map(tmp_path, prog)
    assert planes["moduleTopLevel"] == "native"
    assert planes["classes"]["shapes.Point"] == "island"


def test_island_class_as_a_value_and_kwargs(tmp_path: Path) -> None:
    """The class object taken as a value is a handle from the sibling namespace."""
    prog = tmp_path / "prog"
    _class_shapes(prog)
    _write(
        prog,
        {
            "__main__.py": (
                "from shapes import Point\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    q = Point(x=3, y=4)\n"
                "    cls = Point\n"
                "    print(q.y, cls is Point)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "4 True\n"


def test_package_reexport_of_an_island_class_names_the_owner(tmp_path: Path) -> None:
    """`from pkg import Cls` reaches the class in the defining module's namespace.

    The package `__init__.py` re-exports the class from a sibling (`from
    .shapes import Point`), so the module the import names (`pkg`) is not
    the module whose island namespace holds the class object. The export
    entry carries the owner, and the consumer's call lands there.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__init__.py": "from .shapes import Point\n",
            "shapes.py": (
                "import dataclasses\n"
                "\n"
                "\n"
                "@dataclasses.dataclass(frozen=True, slots=True)\n"
                "class Point:\n"
                "    x: int\n"
                "    y: int\n"
            ),
            "__main__.py": (
                "from prog.shapes import Point\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(Point(1, 2).x)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
    )
    binary, comp = _compile(tmp_path, prog)
    # A package-shaped program runs whole island-side (the split refuses a
    # package tree), so the assertion is the run, not the plane map.
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, "-m", "prog"], capture_output=True, text=True, timeout=60, cwd=prog.parent)
    assert got.returncode == want.returncode == 0, (got.stderr, want.stderr)
    assert got.stdout == want.stdout == "1\n"


def test_native_subclass_of_a_cross_module_island_class_fences_the_subclass(tmp_path: Path) -> None:
    """A native class cannot extend a sibling's island class.

    The island class has no native record, so the subclass definition
    fences and joins the island instead of refusing the compile.
    """
    prog = tmp_path / "prog"
    _class_shapes(prog)
    _write(
        prog,
        {
            "__main__.py": (
                "from shapes import Point\n"
                "\n"
                "\n"
                "class Spot(Point):\n"
                "    pass\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    s = Spot(1, 2)\n"
                "    print(s.x, s.y)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    planes = _plane_map(tmp_path, prog)
    assert planes["classes"]["__main__.Spot"] == "island"
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    assert got.stdout == "1 2\n"


def test_module_owns_its_island_floor_namespace_in_split_mode(tmp_path: Path) -> None:
    """A module's own island definitions ride the namespaced seam.

    The split mode has no single island namespace: every module's floor
    is its own namespace. An island function of the entry module is
    called through `ps_island_mod_fn_invoke` with the entry module's name,
    not the never-seeded single namespace.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    tripled: int = helper(2)\n"
                "    print(tripled)\n"
                "\n"
                "\n"
                "def helper(x):\n"
                "    return x * 3\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    assert "ps_island_mod_fn_invoke" in llvm
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout) == (want.returncode, want.stdout)
    assert got.stdout == "3\n6\n"


def test_island_floor_seed_imports_its_sibling(tmp_path: Path) -> None:
    """A seed whose body imports a sibling finds the sibling's namespace.

    The seeds run in the order CPython finishes the module bodies, so a
    module that imports a sibling is seeded after it.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from mid import relay\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(relay('x'))\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "mid.py": "from leaf import shout\n\n\ndef relay(s):\n    return shout(s + '!')\n",
            "leaf.py": "def shout(s):\n    return s.upper()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    # `leaf` finishes before `mid`, so its seed runs first.
    leaf_pos = llvm.index("ps.fmt.island.mod.leaf")
    mid_pos = llvm.index("ps.fmt.island.mod.mid")
    assert leaf_pos < mid_pos
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout) == (want.returncode, want.stdout)
    assert got.stdout == "X!\n"


def test_an_import_cycle_rides_the_island_and_the_rest_stays_native(tmp_path: Path) -> None:
    """A module-level import cycle does not refuse the compile.

    The three-module shape of the cycle rule: `alpha` and `beta` import
    each other at module level, `clean` sits outside the cycle, and the
    entry calls a clean definition and a cycle definition. The cycle
    members ride the island group (one bootstrap under CPython resolves the
    cycle the way CPython does), the clean module lowers native, and the
    native lane reaches the cycle member through the boxed seam.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from driver import drive\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    print(drive())\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "alpha.py": "def one() -> int:\n    return two() + 1\n\n\nfrom beta import two\n",
            "beta.py": "from alpha import one\n\n\ndef two() -> int:\n    return 10\n",
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "driver.py": "from alpha import one\n\n\ndef drive() -> int:\n    return one()\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "3\n11\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["clean.add"] == "native"
    assert planes["driver.drive"] == "native"
    assert planes["alpha.one"] == "island"
    assert planes["beta.two"] == "island"


def test_the_cycle_group_runs_under_one_bootstrap(tmp_path: Path) -> None:
    """The cycle members ride one bootstrap, not one seed each.

    The group's bootstrap installs a finder over the group's own sources
    and imports every member, so the cycle resolves the way CPython
    resolves it: the module that started the cycle sees the partially
    initialized sibling. A second seed per member would run the bodies
    twice, so the binary carries one group bootstrap and no per-module
    seed for the members.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from driver import drive\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(drive())\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            "alpha.py": "def one() -> int:\n    return two() + 1\n\n\nfrom beta import two\n",
            "beta.py": "from alpha import one\n\n\ndef two() -> int:\n    return 10\n",
            "driver.py": "from alpha import one\n\n\ndef drive() -> int:\n    return one()\n",
        },
    )
    binary, comp = _compile(tmp_path, prog)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    # One group bootstrap, no per-module seed for the two members.
    assert "ps_island_fn_seed" in llvm
    assert "ps_island_mod_seed" not in llvm
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout) == (want.returncode, want.stdout)
    assert got.stdout == "11\n"


def test_a_cycle_member_with_a_module_level_fence_still_runs(tmp_path: Path) -> None:
    """A cycle member's own module-level fences drop with its top level.

    The group's bootstrap execs the real body under CPython, so a lambda
    the checker refuses (PS1001) at module level is CPython's own there:
    the fence is the island's, not the compile's, and the compile stays at
    zero fences with the clean sibling native.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "from clean import add\n"
                "from driver import drive\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    print(drive())\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            # The lambda without a Callable annotation fences the checker;
            # `alpha` and `beta` form the cycle.
            "alpha.py": (
                "wrap = lambda p: p + 1\n\n\ndef one() -> int:\n    return two() + wrap(1)\n\n\nfrom beta import two\n"
            ),
            "beta.py": "from alpha import one\n\n\ndef two() -> int:\n    return 10\n",
            "clean.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
            "driver.py": "from alpha import one\n\n\ndef drive() -> int:\n    return one()\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "3\n12\n"
    planes = _plane_map(tmp_path, prog)["functions"]
    assert planes["clean.add"] == "native"
    assert planes["alpha.one"] == "island"


def test_a_cycle_member_namedtuple_shadowing_a_stdlib_type_does_not_collide(tmp_path: Path) -> None:
    """A cycle member's own namedtuple named like a stdlib type stays island-side.

    The merged class table is program-wide: `Time` is also the stdlib
    `datetime.time` shape the clean modules lower against. The cycle
    member's `collections.namedtuple('Time', ...)` is a different class
    that only its own module sees, so it rides the group bootstrap and
    never enters the native table — a PS2002 duplicate would refuse the
    whole compile.
    """
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": (
                "import collections\n"
                "from clean import add\n"
                "from driver import drive\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    print(add(1, 2))\n"
                "    print(drive())\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            # The cycle: `alpha` declares a namedtuple `Time` at module
            # level; the stdlib `datetime` synthetics also name `Time`.
            "alpha.py": (
                "import collections\n\n\n"
                "_tt = collections.namedtuple('Time', ('hours', 'minutes'))\n\n\n"
                "def one() -> int:\n    return two() + _tt(1, 2).hours\n\n\nfrom beta import two\n"
            ),
            "beta.py": "from alpha import one\n\n\ndef two() -> int:\n    return 10\n",
            # The clean module uses `datetime.time`, which is the stdlib
            # `Time` shape in the native table.
            "clean.py": (
                "import datetime\n\n\ndef add(a: int, b: int) -> int:\n"
                "    t = datetime.time(3)\n    return a + b + t.hour\n"
            ),
            "driver.py": "from alpha import one\n\n\ndef drive() -> int:\n    return one()\n",
        },
    )
    out = _compare(tmp_path, prog)
    assert out == "6\n11\n"
