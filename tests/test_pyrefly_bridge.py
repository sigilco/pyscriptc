"""The pyrefly lane of the checker bridge (issue #258, item 3).

The pyrefly lane is the fallback the checker-bridge design doc names: the
same `CheckerBridge` surface as the mypy lane, fed by `pyrefly tsp`. The
bridge is optional; without pyrefly the lane reports absent and lowering
keeps every fence.
"""

from __future__ import annotations

import ast
import shutil
import textwrap
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.factsbridge import CheckerBridge, PyreflyBridge
from pyscriptc.lower import lower_source

needs_pyrefly = pytest.mark.skipif(shutil.which("pyrefly") is None, reason="pyrefly unavailable")

SOURCE = textwrap.dedent(
    """\
    def typed(x: int) -> int:
        return x

    def inferred_ret(x: int):
        return x + 1

    def main() -> None:
        n = 1 + 2
        print(typed(3), n)
    """
)


def _write(tmp_path: Path, source: str = SOURCE) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return path


@needs_pyrefly
def test_pyrefly_bridge_expression_map(tmp_path: Path) -> None:
    """The bulk request answers the same line:col keys as the mypy lane."""
    facts = PyreflyBridge().facts(_write(tmp_path))
    assert facts.provider == "pyrefly"
    # `n = 1 + 2`: the assignment target carries the inferred type. The bulk
    # request answers `Literal[3]`; `facts_to_ir` widens a literal to its base.
    assert facts.expression_at(8, 4) is not None
    assert facts.expression_at(8, 4).kind in {"int", "literal"}


@needs_pyrefly
def test_pyrefly_bridge_def_facts(tmp_path: Path) -> None:
    """Annotated parameters resolve; an unannotated return is recovered."""
    facts = PyreflyBridge().facts(_write(tmp_path))
    tree = ast.parse(SOURCE)
    defs = {fn.name: fn for fn in tree.body if isinstance(fn, ast.FunctionDef)}
    typed = facts.def_at(defs["typed"].lineno, defs["typed"].col_offset)
    assert typed is not None
    assert typed.params["x"].kind == "int"
    assert typed.return_type is not None and typed.return_type.kind == "int"
    # An unannotated return: pyrefly computes `Any`, the bridge recovers the
    # agreed return type from the return expression's own facts.
    inferred = facts.def_at(defs["inferred_ret"].lineno, defs["inferred_ret"].col_offset)
    assert inferred is not None
    assert inferred.return_type is not None and inferred.return_type.kind == "int"


@needs_pyrefly
def test_pyrefly_bridge_keeps_the_fence_when_the_checker_cannot_prove(tmp_path: Path) -> None:
    """A return that flows an unannotated parameter out stays fenced."""
    source = textwrap.dedent(
        """\
        def leaky(x):
            return x
        """
    )
    facts = PyreflyBridge().facts(_write(tmp_path, source))
    tree = ast.parse(source)
    fn = tree.body[0]
    def_facts = facts.def_at(fn.lineno, fn.col_offset)
    assert def_facts is not None
    assert def_facts.return_type is None
    assert def_facts.params == {}


@needs_pyrefly
def test_checker_bridge_consumer_surface_pyrefly(tmp_path: Path) -> None:
    """The consumer surface is the same for both lanes."""
    bridge = CheckerBridge.for_path(_write(tmp_path), provider="pyrefly")
    assert bridge.provider == "pyrefly"
    assert bridge.inferred_local(8, 4) is not None
    with pytest.raises(CheckFailed):
        CheckerBridge.for_path(_write(tmp_path), provider="pyright")


@needs_pyrefly
def test_pyrefly_bridge_lowering_retires_the_local_fences(tmp_path: Path) -> None:
    """The unannotated local lowers; #500 inference proves `n = 1 + 2` on
    its own, so the plain compile is clean and the bridge retracts nothing."""
    path = _write(tmp_path)
    source = path.read_text(encoding="utf-8")
    plain = lower_source(source, str(path))
    assert plain["fences"] == []
    bridge = CheckerBridge.for_path(path, provider="pyrefly")
    bridged = lower_source(source, str(path), bridge=bridge)
    assert not any(fence["message"].endswith("needs an annotation before use") for fence in bridged["fences"])


def test_pyrefly_version_is_none_without_pyrefly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider version reports absent rather than guessing."""
    from importlib.metadata import PackageNotFoundError

    from pyscriptc import factsbridge

    def _missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(factsbridge, "pyrefly_version", lambda: None)
    assert factsbridge.provider_version("pyrefly") is None
    assert factsbridge.provider_version("nope") is None


def test_checker_bridge_per_file_package_tree(tmp_path: Path) -> None:
    """A package tree builds one bridge per file through the same provider."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .core import answer\n", encoding="utf-8")
    (pkg / "core.py").write_text("def answer() -> int:\n    return 42\n", encoding="utf-8")
    bridge = CheckerBridge.for_path(pkg / "__init__.py")
    inner = bridge.for_file(pkg / "core.py")
    assert inner.provider == bridge.provider
    assert inner is not bridge
