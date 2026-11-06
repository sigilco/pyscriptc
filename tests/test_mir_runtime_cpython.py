"""CPython-baselined runtime check for the MIR lane (epic #817).

The MIR lane's correctness baseline is CPython behavior, not byte parity
with the direct emitter. `scripts/runtime_cpython_ab.py` compiles each
`tests/fixtures/mir_leaf` fixture through the MIR lane, runs the native
binary and the same script under the pinned CPython 3.14 oracle, and
requires stdout, the exit code and the stderr class to match. This is the
check that lets MIR codegen diverge from the old emitter's incidental
shape; deleting the byte-equality assertions is a later PR once this is
green in CI.

Skips cleanly when no C toolchain or no pinned CPython 3.14 oracle is
available, mirroring `tests/test_runtime_ab.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import runtime_ab, runtime_cpython_ab

LEAF_PROGRAMS = [path for path in runtime_ab.leaf_fixture_paths() if runtime_ab.slice_counts(path)[0] > 0]
ORACLE = runtime_cpython_ab.resolve_oracle()

pytestmark = [
    pytest.mark.skipif(not runtime_ab.has_c_toolchain(), reason="no C toolchain"),
    pytest.mark.skipif(ORACLE is None, reason="no pinned CPython 3.14 oracle"),
]


def test_leaf_programs_cover_fixtures() -> None:
    assert LEAF_PROGRAMS, "no in-slice MIR leaf fixtures found"
    names = {path.name for path in LEAF_PROGRAMS}
    assert "leafint.py" in names
    assert "mirmix.py" in names


def test_corpus_scripts_with_slice_are_in_default_set() -> None:
    """The script's default set adds the visible corpus scripts with an in-slice function."""
    defaults = runtime_cpython_ab.default_programs()
    leaf = {path for path in defaults if path.parent == runtime_ab.LEAF_DIR}
    corpus = [path for path in defaults if path.parent.parent == runtime_ab.CORPUS_SCRIPTS]
    assert leaf == set(runtime_ab.leaf_fixture_paths())
    assert corpus, "no visible corpus script has an in-slice function"
    for path in corpus:
        in_slice, _total, _fenced = runtime_cpython_ab.module_stats(path)
        assert in_slice > 0


# Known CPython divergences that are not MIR-lane bugs: `leafmap.py`
# exercises `str.rsplit` with `maxsplit`, which the shared runtime
# (`ps_str_split_m`) gets wrong on both lanes (#1052); the three corpus
# scripts overflow the documented i64 `int` (divergence 1) where CPython
# has bigint. Keys are path suffixes so corpus `main.py`s disambiguate.
# The xfail is strict so the marker must be removed once fixed.
_KNOWN_DIVERGENT = {
    "leafmap.py": "str.rsplit ignores maxsplit (#1052)",
    "bigint_factorial/main.py": "int is i64, CPython has bigint (divergence 1)",
    "gcd_lcm/main.py": "int is i64, CPython has bigint (divergence 1)",
    "pi_digits/main.py": "int is i64, CPython has bigint (divergence 1)",
}


def known_divergence(path: Path) -> str | None:
    """The documented-divergence reason for `path`, or None."""
    text = str(path)
    for suffix, reason in _KNOWN_DIVERGENT.items():
        if text.endswith(suffix):
            return reason
    return None


def _leaf_params() -> list[object]:
    params: list[object] = []
    for path in LEAF_PROGRAMS:
        reason = known_divergence(path)
        if reason is not None:
            params.append(pytest.param(path, marks=pytest.mark.xfail(reason=reason, strict=True)))
        else:
            params.append(path)
    return params


@pytest.mark.parametrize("path", _leaf_params(), ids=[path.stem for path in LEAF_PROGRAMS])
def test_mir_lane_matches_cpython(path: Path) -> None:
    row = runtime_cpython_ab.compare_program(path, oracle=ORACLE)
    assert row.verdict == runtime_cpython_ab.PASS, f"{path.name}: {row.verdict} ({row.note})"
