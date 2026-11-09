"""Direct vs `PYSC_MIR=1` differential smoke over the in-slice programs.

Every visible corpus script with at least one function in the MIR leaf slice
(per `scripts/mir_coverage.py`), plus the `test_mir_leaf` fixtures, compiles
through the CLI to textual LLVM twice: once on the default direct path and
once with the `PYSC_MIR` escape hatch on. Each `define` must be byte
identical, so a function that diverges is named in the failure.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from tests.test_mir_leaf import BYTE_IDENTITY, LEAF_SAMPLES

from pyscriptc.cli import main as cli_main
from pyscriptc.lower import lower_file
from pyscriptc.miremit import MIR_ENV

ROOT = Path(__file__).resolve().parent.parent
_COVERAGE = ROOT / "scripts" / "mir_coverage.py"
_DEFINE = re.compile(r"^define [^@]*@\"?([^\"(]+)\"?\(", re.MULTILINE)


def _coverage_module():
    spec = importlib.util.spec_from_file_location("mir_coverage", _COVERAGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_fences(path: Path) -> bool:
    """A fenced script fails the direct compile too, so it has nothing to diff."""
    module, _ = lower_file(str(path))
    return bool(module["fences"])


def _in_slice_corpus() -> list[tuple[str, list[str]]]:
    mod = _coverage_module()
    out: list[tuple[str, list[str]]] = []
    for path in mod.script_mains(mod.DEFAULT_SCRIPTS):
        row = mod.measure_script(path)
        names = row["in_slice_names"]
        if names and not _has_fences(path):
            out.append((str(path), list(names)))
    return out


IN_SLICE_CORPUS = _in_slice_corpus()


def _defines(ll: str) -> dict[str, str]:
    """`define` bodies keyed by symbol, each up to its closing brace."""
    out: dict[str, str] = {}
    for match in _DEFINE.finditer(ll):
        end = ll.find("\n}\n", match.start())
        out[match.group(1)] = ll[match.start() : end + 3]
    return out


def _compile_ll(src: Path, out: Path, monkeypatch: pytest.MonkeyPatch, mir: bool) -> str:
    if mir:
        monkeypatch.setenv(MIR_ENV, "1")
    else:
        monkeypatch.delenv(MIR_ENV, raising=False)
    rc = cli_main(["compile", str(src), "--emit", "llvm", "-o", str(out)])
    assert rc == 0
    return out.read_text(encoding="utf-8")


def _assert_cpython_baseline(src: Path) -> None:
    """PYSC_MIR_BYTE_IDENTITY=0: the CPython-baselined check replaces byte equality."""
    from scripts import runtime_ab, runtime_cpython_ab
    from tests.test_mir_runtime_cpython import known_divergence

    if not runtime_ab.has_c_toolchain():
        pytest.skip("no C toolchain for the CPython-baselined runtime check")
    oracle = runtime_cpython_ab.resolve_oracle()
    if oracle is None:
        pytest.skip("no pinned CPython 3.14 oracle for the CPython-baselined runtime check")
    row = runtime_cpython_ab.compare_program(src, oracle=oracle)
    reason = known_divergence(src)
    if reason is not None and row.verdict != runtime_cpython_ab.PASS:
        pytest.xfail(f"{src.name}: {row.verdict} ({row.note}); {reason}")
    assert row.verdict == runtime_cpython_ab.PASS, f"{src.name}: {row.verdict} ({row.note})"


def _assert_identical(src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    direct = _compile_ll(src, tmp_path / "direct.ll", monkeypatch, mir=False)
    mir = _compile_ll(src, tmp_path / "mir.ll", monkeypatch, mir=True)
    d_defs, m_defs = _defines(direct), _defines(mir)
    assert sorted(m_defs) == sorted(d_defs)
    if not BYTE_IDENTITY:
        _assert_cpython_baseline(src)
    else:
        for name in d_defs:
            assert m_defs[name] == d_defs[name], f"{src.name}: @{name} differs between direct and {MIR_ENV}=1"
        assert mir == direct
    return d_defs


def test_corpus_has_in_slice_programs() -> None:
    assert IN_SLICE_CORPUS, "no visible corpus script has an in-slice function"


@pytest.mark.parametrize(("path", "names"), IN_SLICE_CORPUS, ids=[Path(p).parent.name for p, _ in IN_SLICE_CORPUS])
def test_corpus_direct_vs_pysc_mir(
    path: str, names: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    defines = _assert_identical(Path(path), tmp_path, monkeypatch)
    for name in names:
        assert any(sym == name or sym.endswith(f".{name}") or sym.endswith(f"_{name}") for sym in defines), name
    capsys.readouterr()


@pytest.mark.parametrize("path", LEAF_SAMPLES, ids=[Path(p).stem for p in LEAF_SAMPLES])
def test_leaf_fixture_direct_vs_pysc_mir(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _assert_identical(Path(path), tmp_path, monkeypatch)
    capsys.readouterr()
