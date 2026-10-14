"""A directory compiles as one module, and relative imports resolve inside it."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, run_cli


def test_relative_imports_resolve_in_a_directory(tmp_path: Path) -> None:
    root = tmp_path / "labeler"
    (root / "sub").mkdir(parents=True)
    (root / "__init__.py").write_text("VERSION: str = 'v1'\n", encoding="utf-8")
    (root / "util.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "\n".join(
            [
                "from .util import add",
                "from . import VERSION",
                "",
                "def show(n: int) -> str:",
                "    return VERSION + ' ' + str(add(n, 1))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (root / "sub" / "extra.py").write_text(
        "\n".join(
            [
                "from ..util import add",
                "",
                "def twice(n: int) -> int:",
                "    return add(n, n)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    got = compile_run(tmp_path, root, "labeler.app.show", "[2]")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "v1 3\n"
    twice = compile_run(tmp_path, root, "labeler.sub.extra.twice", "[4]")
    assert twice.returncode == 0, twice.stderr
    assert twice.stdout == "8\n"


def test_absolute_imports_of_this_package_resolve(tmp_path: Path) -> None:
    root = tmp_path / "labeler"
    root.mkdir()
    (root / "__init__.py").write_text("VERSION: str = 'v1'\n", encoding="utf-8")
    (root / "util.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "\n".join(
            [
                "from labeler.util import add",
                "from labeler import VERSION",
                "import labeler.util as util",
                "",
                "def show(n: int) -> str:",
                "    return VERSION + ' ' + str(util.add(n, add(n, 0)))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    got = compile_run(tmp_path, root, "labeler.app.show", "[2]")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "v1 4\n"


def test_directory_exports_a_class_and_an_exception(tmp_path: Path) -> None:
    root = tmp_path / "shapes"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "point.py").write_text(
        "\n".join(
            [
                "class Point:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "err.py").write_text(
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "def boom() -> int:",
                "    raise AppError('x')",
                "    return 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "\n".join(
            [
                "from .point import Point",
                "from .err import AppError, boom",
                "from shapes.point import Point as Dot",
                "",
                "def read(n: int) -> int:",
                "    return Point(n).x + Dot(1).x",
                "",
                "def catch() -> int:",
                "    try:",
                "        boom()",
                "    except AppError:",
                "        return 7",
                "    return 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    got = compile_run(tmp_path, root, "shapes.app.read", "[4]")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "5\n"
    caught = compile_run(tmp_path, root, "shapes.app.catch")
    assert caught.returncode == 0, caught.stderr
    assert caught.stdout == "7\n"


def test_differing_classes_with_one_name_still_fence(tmp_path: Path) -> None:
    root = tmp_path / "clash"
    root.mkdir()
    (root / "a.py").write_text(
        "class Box:\n    n: int\n    def __init__(self, n: int) -> None:\n        self.n = n\n",
        encoding="utf-8",
    )
    (root / "b.py").write_text(
        "class Box:\n    label: str\n    def __init__(self, label: str) -> None:\n        self.label = label\n",
        encoding="utf-8",
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "clash.ir.json"))
    assert got.returncode == 3
    assert "defined in more than one module" in got.stderr


def test_a_subclass_in_another_module_is_exported(tmp_path: Path) -> None:
    root = tmp_path / "shapes"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "point.py").write_text(
        "\n".join(
            [
                "class Point:",
                "    x: int",
                "    def __init__(self, x: int) -> None:",
                "        self.x = x",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "dot.py").write_text(
        "\n".join(
            [
                "from .point import Point",
                "",
                "class Dot(Point):",
                "    def read(self) -> int:",
                "        return self.x",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "\n".join(
            [
                "from .dot import Dot",
                "",
                "def show(n: int) -> int:",
                "    return Dot(n).read()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    got = compile_run(tmp_path, root, "shapes.app.show", "[4]")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "4\n"


def test_a_definition_fence_is_reported_before_the_export(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("from .bad import Broken\n", encoding="utf-8")
    (root / "bad.py").write_text(
        "class Broken(Missing):\n    def __init__(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    got = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "pkg.ir.json"))
    assert got.returncode == 3, got.stderr
    first = next(line for line in got.stderr.splitlines() if "PS" in line)
    assert "this class base is not lowered" in first
    assert "not exported" not in first


def test_dunder_main_is_skipped_and_a_library_import_still_fences(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "util.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left + right\n",
        encoding="utf-8",
    )
    (root / "__main__.py").write_text("import argparse\n", encoding="utf-8")
    got = compile_run(tmp_path, root, "pkg.util.add", "[2, 2]")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "4\n"
    assert "skipped pkg/__main__.py" in got.stderr
    assert "not an importable module" in got.stderr
    (root / "cli.py").write_text("import argparse\n", encoding="utf-8")
    refused = run_cli("compile", str(root), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "refused.ir.json"))
    assert refused.returncode == 3, refused.stderr
    assert "argparse" in refused.stderr


def test_a_single_file_still_refuses_a_relative_import(tmp_path: Path) -> None:
    path = tmp_path / "alone.py"
    path.write_text("from .util import add\n\ndef show() -> int:\n    return add(1, 1)\n", encoding="utf-8")
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "alone.ll"))
    assert got.returncode == 3
    assert "relative import" in got.stderr
