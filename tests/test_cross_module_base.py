# SPDX-License-Identifier: Apache-2.0
"""The cross-module class base (issue #732).

A class that extends a class from another module of the same program
lowers natively: the subclass inherits the base's fields and methods, and
the inherited method keeps the definer that owns its body, so the call
site reaches `cls.{Base}.{method}` and not a symbol no module emits.

The export table carries the class's MRO list and its primary-base spine
for exactly this: the consumer resolves an inherited method's owner
through them. The inheritance merge also reads the base's own MRO, so a
method that reached the subclass through two modules keeps its real owner
instead of being dropped.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BASE = (
    "class Base:\n"
    "    def __init__(self, x: int) -> None:\n"
    "        self.x = x\n"
    "\n"
    "    def get_x(self) -> int:\n"
    "        return self.x\n"
    "\n"
    "    def tag(self) -> str:\n"
    '        return "base:" + str(self.x)\n'
)

CHILD = (
    "from base import Base\n"
    "\n"
    "\n"
    "class Child(Base):\n"
    "    def __init__(self, x: int) -> None:\n"
    "        super().__init__(x)\n"
    "\n"
    "    def tag(self) -> str:\n"
    '        return "child:" + str(self.get_x())\n'
)

ENTRY = (
    "from child import Child\n"
    "\n"
    "\n"
    "def main() -> None:\n"
    "    c: Child = Child(3)\n"
    "    print(c.tag())\n"
    "    print(c.get_x())\n"
    "\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def _program(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "prog"
    _write(root, files)
    return root


def _compile(tmp_path: Path, root: Path) -> tuple[Path, subprocess.CompletedProcess[str]]:
    binary = tmp_path / "bin"
    comp = _cli("compile", str(root), "--program", "--emit", "bin", "-o", str(binary))
    return binary, comp


def _compare(tmp_path: Path, root: Path) -> str:
    binary, comp = _compile(tmp_path, root)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run(
        [sys.executable, str(root / "__main__.py")], capture_output=True, text=True, timeout=60, cwd=root
    )
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    return got.stdout


def test_a_cross_module_base_lowers_and_matches_cpython(tmp_path: Path) -> None:
    """The subclass inherits the base's fields and methods, natively."""
    root = _program(tmp_path, {"__main__.py": ENTRY, "base.py": BASE, "child.py": CHILD})
    out = _compare(tmp_path, root)
    assert out == "child:3\n3\n"


def test_the_inherited_method_keeps_the_defining_class_as_its_owner(tmp_path: Path) -> None:
    """The emitted symbol is `cls.Base.get_x`, the one the base's module emits.

    The subclass does not define `get_x`, so the call site must reach the
    base's body. A symbol `cls.Child.get_x` has no body anywhere, and the
    compile stops with the emitter's planning hole.
    """
    root = _program(tmp_path, {"__main__.py": ENTRY, "base.py": BASE, "child.py": CHILD})
    binary, comp = _compile(tmp_path, root)
    assert comp.returncode == 0, comp.stderr
    llvm = (binary.parent / f"{binary.name}.ll").read_text(encoding="utf-8")
    assert "define i64 @cls.Base.get_x" in llvm
    assert "call i64 @cls.Base.get_x" in llvm
    assert "cls.Child.get_x" not in llvm


def test_the_class_export_carries_the_mro_and_the_base_spine(tmp_path: Path) -> None:
    """The export table's class row carries the MRO list and the spine.

    The consumer resolves an inherited method's owner through them, so a
    row without them loses the inherited methods.
    """
    from pyscriptc.package import _catalog

    root = _program(tmp_path, {"__main__.py": ENTRY, "base.py": BASE, "child.py": CHILD})
    units = [
        ("__main__", root / "__main__.py", False),
        ("base", root / "base.py", False),
        ("child", root / "child.py", False),
    ]
    exports, _failed = _catalog(units, True, entry=None)
    info = exports["child"]["Child"]["info"]
    assert info["mro"] == ["Child", "Base"]
    assert sorted(info["chain"]) == ["Base", "Child"]
    # The inherited method names the class that owns its body.
    assert info["method_owner"]["get_x"] == "Base"


def test_a_method_inherited_through_two_modules_keeps_its_owner(tmp_path: Path) -> None:
    """`Child -> Mid -> Base`: the inherited method survives the chain.

    The base's export says `get_x` is owned by `Base`, two modules up from
    the consumer. The inheritance merge reads the ancestor's own MRO, so
    the method is inherited with its real owner and the call site reaches
    the body the base's module emits.
    """
    mid = 'from base import Base\n\n\nclass Mid(Base):\n    def label(self) -> str:\n        return "mid"\n'
    child = (
        "from mid import Mid\n"
        "\n"
        "\n"
        "class Child(Mid):\n"
        "    def __init__(self, x: int) -> None:\n"
        "        super().__init__(x)\n"
        "\n"
        "    def tag(self) -> str:\n"
        '        return "child>" + self.label() + str(self.get_x())\n'
    )
    root = _program(tmp_path, {"__main__.py": ENTRY, "base.py": BASE, "mid.py": mid, "child.py": child})
    out = _compare(tmp_path, root)
    assert out == "child>mid3\n3\n"


def test_a_package_shaped_tree_lowers_the_cross_module_base(tmp_path: Path) -> None:
    """The `pkg.mod` spelling resolves the same way."""
    root = tmp_path / "prog"
    _write(
        root,
        {
            "pkg/__init__.py": "",
            "pkg/base.py": BASE,
            "pkg/child.py": (
                "from pkg.base import Base\n"
                "\n"
                "\n"
                "class Child(Base):\n"
                "    def __init__(self, x: int) -> None:\n"
                "        super().__init__(x)\n"
            ),
            "__main__.py": (
                "from pkg.child import Child\n"
                "\n"
                "\n"
                "def main() -> None:\n"
                "    c: Child = Child(3)\n"
                "    print(c.get_x())\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        },
    )
    out = _compare(tmp_path, root)
    assert out == "3\n"


def test_the_program_plane_map_keeps_the_classes_native(tmp_path: Path) -> None:
    """The base and the subclass stay on the native lane.

    The plane map records the island lane only, so a class the map does
    not name never left the native lane.
    """
    root = _program(tmp_path, {"__main__.py": ENTRY, "base.py": BASE, "child.py": CHILD})
    binary, comp = _compile(tmp_path, root)
    assert comp.returncode == 0, comp.stderr
    report = json.loads((binary.parent / f"{binary.name}.report.json").read_text(encoding="utf-8"))
    planes = report["planeMap"]
    assert "base.Base" not in planes["classes"]
    assert "child.Child" not in planes["classes"]
    assert planes["functions"]["__main__.main"] == "native"
