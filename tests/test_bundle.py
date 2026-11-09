"""Unit tests for the AST-level project bundler (src/pyscriptc/bundle.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyscriptc.bundle import BundleError, bundle_project


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_topological_order(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", "from lib.helper import value\nprint(value)\n")
    _write(tmp_path, "lib/__init__.py", "X = 1\n")
    _write(tmp_path, "lib/helper.py", "from lib.base import TWO\nvalue = TWO\n")
    _write(tmp_path, "lib/base.py", "TWO = 2\n")
    bundle = bundle_project(tmp_path / "app.py")
    assert bundle.order[-1] == "app"
    assert bundle.order.index("lib.base") < bundle.order.index("lib.helper")
    assert "lib__base__TWO = 2" in bundle.source
    assert "lib__helper__value = lib__base__TWO" in bundle.source


def test_ambiguous_import_name_refused(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "from a import config\nfrom b import config\nx = config\n")
    _write(tmp_path, "a.py", "config = 1\n")
    _write(tmp_path, "b.py", "config = 2\n")
    with pytest.raises(BundleError) as exc:
        bundle_project(tmp_path / "main.py")
    assert "as" in str(exc.value)


def test_aliased_imports_disambiguate(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "main.py",
        "from a import config as a_config\nfrom b import config as b_config\nx = a_config + b_config\n",
    )
    _write(tmp_path, "a.py", "config = 1\n")
    _write(tmp_path, "b.py", "config = 2\n")
    bundle = bundle_project(tmp_path / "main.py")
    assert "x = a__config + b__config" in bundle.source


def test_cycle_refused(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "import a\n")
    _write(tmp_path, "a.py", "import b\n")
    _write(tmp_path, "b.py", "import a\n")
    with pytest.raises(BundleError) as exc:
        bundle_project(tmp_path / "main.py")
    assert "cycle" in str(exc.value)
    assert "a" in str(exc.value) and "b" in str(exc.value)


def test_line_map_round_trip(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "import helper\n")
    _write(tmp_path, "helper.py", "\n\ndef greet() -> int:\n    return 7\n")
    bundle = bundle_project(tmp_path / "main.py")
    helper_path = tmp_path / "helper.py"
    for bundled_line, (file, orig_line) in bundle.line_map.items():
        text = bundle.source.splitlines()[bundled_line - 1]
        if "return 7" in text:
            assert file == str(helper_path)
            assert Path(file).read_text().splitlines()[orig_line - 1] == "    return 7"


def test_stays_on_entry_reachable_modules(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "import used\nprint(used.VALUE)\n")
    _write(tmp_path, "used.py", "VALUE = 1\n")
    _write(tmp_path, "unused.py", "VALUE = 2\n")
    bundle = bundle_project(tmp_path / "main.py")
    assert bundle.order == ["used", "main"]
    assert "unused__VALUE" not in bundle.source


def test_missing_entry_refused(tmp_path: Path) -> None:
    with pytest.raises(BundleError):
        bundle_project(tmp_path / "nope.py")


def test_import_star_refused(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "from helper import *\n")
    _write(tmp_path, "helper.py", "VALUE = 1\n")
    with pytest.raises(BundleError) as exc:
        bundle_project(tmp_path / "main.py")
    assert "import *" in str(exc.value)
