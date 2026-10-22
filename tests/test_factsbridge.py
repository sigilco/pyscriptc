"""Checker bridge (issue #87): mypy's inferred-type map into lowering.

The bridge is optional. Without mypy importable it reports absent and every
consumer no-ops; with mypy it answers lowering's inference questions through
the plain `TypeFacts` map, never mypy objects.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from pyscriptc.check import CheckFailed
from pyscriptc.factsbridge import CheckerBridge, MypyBridge, mypy_available
from pyscriptc.lower import lower_source

needs_mypy = pytest.mark.skipif(not mypy_available(), reason="mypy >=2.4,<2.5 not installed")

SOURCE = textwrap.dedent(
    """\
    def scaled(v=2):
        return v * 10

    def typed(x: int) -> int:
        return x

    def inferred_ret(x: int):
        return x + 1

    def main() -> None:
        y = typed(3)
        s = "hi"
        print(y, s)
    """
)


def _write(tmp_path: Path, source: str = SOURCE) -> Path:
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_bridge_absent_without_mypy(monkeypatch: pytest.MonkeyPatch) -> None:
    """With mypy masked out, the bridge reports absent instead of failing."""
    monkeypatch.setitem(sys.modules, "mypy", None)
    monkeypatch.setitem(sys.modules, "mypy.build", None)
    monkeypatch.setitem(sys.modules, "mypy.options", None)
    fresh = importlib.reload(importlib.import_module("pyscriptc.factsbridge"))
    try:
        assert fresh.mypy_available() is False
    finally:
        importlib.reload(importlib.import_module("pyscriptc.factsbridge"))


@needs_mypy
def test_mypy_bridge_expression_map(tmp_path: Path) -> None:
    facts = MypyBridge().facts(_write(tmp_path))
    assert facts.provider == "mypy"
    # `y = scaled(3)`: the assignment target NameExpr carries the inferred type.
    assert facts.expression_at(11, 4) is not None
    assert facts.expression_at(11, 4).kind == "int"
    assert facts.expression_at(12, 4).kind == "str"


@needs_mypy
def test_mypy_bridge_def_facts(tmp_path: Path) -> None:
    facts = MypyBridge().facts(_write(tmp_path))
    tree = __import__("ast").parse(SOURCE)
    defs = {fn.name: fn for fn in tree.body if isinstance(fn, __import__("ast").FunctionDef)}
    scaled = facts.def_at(defs["scaled"].lineno, defs["scaled"].col_offset)
    assert scaled is not None
    # mypy cannot prove `v` from an unannotated default (it is Any), so the
    # bridge leaves it out rather than inventing one.
    assert scaled.params == {}
    typed = facts.def_at(defs["typed"].lineno, defs["typed"].col_offset)
    assert typed.params["x"].kind == "int"
    assert typed.return_type is not None
    # An unannotated return with agreeing return statements is inferred.
    inferred = facts.def_at(defs["inferred_ret"].lineno, defs["inferred_ret"].col_offset)
    assert inferred.return_type is not None
    assert inferred.return_type.kind == "int"


@needs_mypy
def test_mypy_bridge_errors_surface_as_check_failed(tmp_path: Path) -> None:
    path = _write(tmp_path, "def f(x: int) -> str:\n    return x\n")
    with pytest.raises(CheckFailed):
        MypyBridge().facts(path)


@needs_mypy
def test_checker_bridge_consumer_surface(tmp_path: Path) -> None:
    bridge = CheckerBridge.for_path(_write(tmp_path))
    assert bridge.provider == "mypy"
    assert bridge.inferred_local(11, 4) is not None
    with pytest.raises(CheckFailed):
        CheckerBridge.for_path(_write(tmp_path), provider="pyright")


def test_import_without_mypy() -> None:
    """`pyscriptc.factsbridge` imports with mypy absent; the zero-dependency path is unchanged."""
    blocked = (
        "import sys\n"
        "for name in [m for m in sys.modules if m == 'mypy' or m.startswith('mypy.')]:\n"
        "    del sys.modules[name]\n"
        "sys.modules['mypy'] = None\n"
        "import pyscriptc.factsbridge as bridge\n"
        "assert bridge.mypy_available() is False\n"
    )
    proc = subprocess.run([sys.executable, "-c", blocked], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --- lowering consumption ----------------------------------------------------

LOCAL_SOURCE = textwrap.dedent(
    """\
    def main() -> None:
        total = 1 + 2
        label = "hello"
        print(total, label)
    """
)

RETURN_SOURCE = textwrap.dedent(
    """\
    def branch(x: int):
        if x:
            return 1
        return 2

    def mixed(x: int):
        if x:
            return 1
        return "s"

    def main() -> None:
        print(branch(1), mixed(2))
    """
)


def _fences(module: dict) -> list[str]:
    return [fence["message"] for fence in module["fences"]]


def test_no_bridge_keeps_the_inference_fences() -> None:
    """Without a bridge the IR and the fences are exactly what they were."""
    module = lower_source(LOCAL_SOURCE, "probe.py")
    assert "total needs an annotation before use" in _fences(module)
    assert "label needs an annotation before use" in _fences(module)


@needs_mypy
def test_bridge_unfences_local_inference(tmp_path: Path) -> None:
    path = _write(tmp_path, LOCAL_SOURCE)
    module = lower_source(LOCAL_SOURCE, str(path), CheckerBridge.for_path(path))
    assert _fences(module) == []
    locals_ = {local["name"]: local["type"] for local in module["functions"][0]["locals"]}
    assert locals_["total"] == {"kind": "int"}
    assert locals_["label"] == {"kind": "str"}


@needs_mypy
def test_bridge_infers_return_only_when_the_returns_agree(tmp_path: Path) -> None:
    path = _write(tmp_path, RETURN_SOURCE)
    module = lower_source(RETURN_SOURCE, str(path), CheckerBridge.for_path(path))
    messages = _fences(module)
    # `branch` returns int in every branch: the bridge answers.
    assert "branch needs a return annotation" not in messages
    # `mixed` returns int then str: mypy proves no single type, the fence stands.
    assert "mixed needs a return annotation" in messages
    returns = {fn["name"]: fn["returnType"] for fn in module["functions"]}
    assert returns["branch"] == {"kind": "int"}


@needs_mypy
def test_bridge_off_leaves_the_ir_unchanged(tmp_path: Path) -> None:
    """The bridged compile of an already-annotated file is the plain compile."""
    source = textwrap.dedent(
        """\
        def main() -> None:
            total: int = 1 + 2
            print(total)
        """
    )
    path = _write(tmp_path, source)
    plain = lower_source(source, str(path))
    bridged = lower_source(source, str(path), CheckerBridge.for_path(path))
    assert plain == bridged


@needs_mypy
def test_cli_bridge_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`compile --bridge mypy --emit ir` writes IR instead of fencing."""
    from pyscriptc import cli

    path = _write(tmp_path, LOCAL_SOURCE)
    code = cli.main(["compile", str(path), "--skip-check", "--bridge", "mypy", "--emit", "ir"])
    assert code == 0
    ir_path = Path(capsys.readouterr().out.strip())
    assert ir_path.exists()


@needs_mypy
def test_cli_without_bridge_fences(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from pyscriptc import cli

    path = _write(tmp_path, LOCAL_SOURCE)
    assert cli.main(["compile", str(path), "--skip-check", "--emit", "ir"]) == 3
    assert "needs an annotation before use" in capfd.readouterr().err
