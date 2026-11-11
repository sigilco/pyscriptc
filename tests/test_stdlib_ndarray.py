"""The pyscriptc_ndarray M1 surface (issue #496).

The compile-run cases diff the compiled program against the pinned
CPython. The oracle for `pyscriptc_ndarray` is not real numpy (the
surface mirrors numpy's semantics on a closed subset, but the module is
pyscriptc-native), so the oracle programs rewrite the calls onto plain
lists: the values the compiled run prints must equal what the same
operations produce on CPython floats and ints. The float formatting of
`tolist()` rides the runtime's repr, so the fixture compares it too.

Every fence case checks the PS2001 naming of the module, or the
PS100x/PS200x of the arguments outside the subset.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from pyscriptc.lower import lower_source

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    # `--call run` echoes the return value on its own line; the cases all
    # return 0, so the echo is a constant suffix.
    assert got.stdout.endswith("\n0\n"), got.stdout
    return got.stdout[: -len("\n0\n")] + "\n"


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


# ---------------------------------------------------------------------
# creation and the value fixture
# ---------------------------------------------------------------------


def test_creation_matches_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_creation.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    print(np.array('float64', [1.0, 2.5, 3.0]).tolist())",
                "    print(np.array('int32', [1, 2, 3]).tolist())",
                "    print(np.zeros([3, 4], dtype='int32').tolist())",
                "    print(np.ones(3).tolist())",
                "    print(np.full([2, 3], 7).tolist())",
                "    print(np.full([2, 2], 2.5, dtype='float64').tolist())",
                "    print(np.arange(0, 10, 2).tolist())",
                "    print(np.arange(5).tolist())",
                "    print(np.arange(0.0, 1.0, 0.25).tolist())",
                "    print(np.linspace(0.0, 1.0, 5).tolist())",
                "    print(np.fromlist([4, 5, 6], 'float64').tolist())",
                "    return 0",
            ]
        ),
    )
    a = np_linspace(0.0, 1.0, 5)
    ar = np_arange(0.0, 1.0, 0.25)
    assert got == (
        "[1.0, 2.5, 3.0]\n"
        "[1, 2, 3]\n"
        "[[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]\n"
        "[1.0, 1.0, 1.0]\n"
        "[[7, 7, 7], [7, 7, 7]]\n"
        "[[2.5, 2.5], [2.5, 2.5]]\n"
        "[0, 2, 4, 6, 8]\n"
        "[0, 1, 2, 3, 4]\n"
        f"[{ar[0]}, {ar[1]}, {ar[2]}, {ar[3]}]\n"
        f"[{a[0]}, {a[1]}, {a[2]}, {a[3]}, {a[4]}]\n"
        "[4.0, 5.0, 6.0]\n"
    )


def np_arange(start: float, stop: float, step: float) -> list[float]:
    out: list[float] = []
    v = start
    while v < stop:
        out.append(v)
        v += step
    return out


def np_linspace(start: float, stop: float, num: int) -> list[float]:
    return [start + (stop - start) * i / (num - 1) for i in range(num)]


def test_attributes_and_dtype_names(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_attrs.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    z = np.zeros([3, 4], dtype='int32')",
                "    print(z.shape, z.ndim, z.size, z.nbytes, z.dtype)",
                "    a = np.array('float32', [1.0])",
                "    print(a.dtype, a.size, a.nbytes)",
                "    return 0",
            ]
        ),
    )
    assert got == "[3, 4] 2 12 48 int32\nfloat32 1 4\n"


# ---------------------------------------------------------------------
# elementwise arithmetic, the numpy promotion table
# ---------------------------------------------------------------------


def test_elementwise_arithmetic_matches_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_arith.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('int32', [1, 2, 3])",
                "    b = np.array('int32', [4, 5, 6])",
                "    print((a + b).tolist(), (b - a).tolist(), (a * b).tolist())",
                "    print((b // a).tolist(), (b % a).tolist())",
                "    f = np.array('float64', [1.0, 4.0, 9.0])",
                "    print((f / 2.0).tolist(), (f * 2).tolist(), (f + 1.5).tolist())",
                "    print((a / 2).tolist(), (f // 2.0).tolist())",
                "    mixed = np.array('int64', [1, 2, 3]) * 1.5",
                "    print(mixed.tolist(), mixed.dtype)",
                "    return 0",
            ]
        ),
    )
    assert got == (
        "[5, 7, 9] [3, 3, 3] [4, 10, 18]\n"
        "[4, 2, 2] [0, 1, 0]\n"
        "[0.5, 2.0, 4.5] [2.0, 8.0, 18.0] [2.5, 5.5, 10.5]\n"
        "[0.5, 1.0, 1.5] [0.0, 2.0, 4.0]\n"
        "[1.5, 3.0, 4.5] float64\n"
    )


def test_comparisons_produce_bool_arrays(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_cmp.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('int32', [1, 2, 3])",
                "    b = np.array('int32', [2, 2, 1])",
                "    print((a == b).tolist(), (a != b).tolist())",
                "    print((a < b).tolist(), (a <= b).tolist(), (a > b).tolist(), (a >= b).tolist())",
                "    print((a > 1).tolist())",
                "    print(f_a())",
                "    return 0",
                "",
                "def f_a() -> list[bool]:",
                "    x: np.ndarray = np.array('float64', [0.5, 2.5])",
                "    return (x >= 1.0).tolist()",
            ]
        ),
    )
    assert got == (
        "[False, True, False] [True, False, True]\n"
        "[True, False, False] [True, True, False] [False, False, True] [False, True, True]\n"
        "[False, True, True]\n"
        "[False, True]\n"
    )


# ---------------------------------------------------------------------
# reductions
# ---------------------------------------------------------------------


def test_reductions_match_cpython(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_reduce.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('float64', [1.0, 2.0, 3.0, 4.0])",
                "    print(a.sum(), a.min(), a.max(), a.mean(), a.argmin(), a.argmax())",
                f'    print(f"{{a.std():.12f}} {{a.var():.12f}}")',
                "    i = np.array('int64', [3, 1, 2])",
                "    print(i.sum(), i.min(), i.max())",
                "    m = np.array('float64', [1.0, 2.0, 3.0, 4.0]).reshape([2, 2])",
                "    print(m.sum(axis=0).tolist(), m.sum(axis=1).tolist())",
                "    print(m.mean(axis=1).tolist(), m.max(axis=0).tolist())",
                "    return 0",
            ]
        ),
    )
    vals = [1.0, 2.0, 3.0, 4.0]
    mean = sum(vals) / 4
    var = sum((v - mean) ** 2 for v in vals) / 4
    assert got == (
        "10.0 1.0 4.0 2.5 0 3\n"
        f"{math.sqrt(var):.12f} {var:.12f}\n"
        "6 1 3\n"
        "[4.0, 6.0] [3.0, 7.0]\n"
        "[1.5, 3.5] [3.0, 4.0]\n"
    )


# ---------------------------------------------------------------------
# shape operations
# ---------------------------------------------------------------------


def test_shape_ops(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_shape.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('float64', [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]).reshape([2, 3])",
                "    print(a.tolist())",
                "    print(a.transpose().tolist())",
                "    print(a.ravel().tolist())",
                "    print(a.astype('int32').tolist(), a.astype('int32').dtype)",
                "    print(a.copy().tolist(), a.reshape([3, 2]).tolist())",
                "    print(a.ravel().shape, a.transpose().shape)",
                "    return 0",
            ]
        ),
    )
    assert got == (
        "[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]\n"
        "[[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]]\n"
        "[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]\n"
        "[[1, 2, 3], [4, 5, 6]] int32\n"
        "[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]] [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]\n"
        "[6] [3, 2]\n"
    )


# ---------------------------------------------------------------------
# step slicing and bool-mask indexing
# ---------------------------------------------------------------------


def test_step_slice_and_bool_mask(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_index.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('int64', [1, 2, 3, 4, 5])",
                "    print(a[1:4:2].tolist(), a[::2].tolist(), a[2:].tolist())",
                "    print(a[a > 2].tolist(), a[(a % 2 == 1)].tolist())",
                "    z = np.array('int64', [0, 0, 0])",
                "    z[1] = 9",
                "    print(z.tolist(), z[1], z[-1])",
                "    f = np.array('float64', [0.5, 1.5, 2.5])",
                "    f[0] = 9.5",
                "    print(f.tolist(), f[2])",
                "    return 0",
            ]
        ),
    )
    assert got == "[2, 4] [1, 3, 5] [3, 4, 5]\n[3, 4, 5] [1, 3, 5]\n[0, 9, 0] 9 0\n[9.5, 1.5, 2.5] 2.5\n"


# ---------------------------------------------------------------------
# list and bytes conversions
# ---------------------------------------------------------------------


def test_tolist_tobytes_fromlist(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_convert.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('int64', [1, 2, 3])",
                "    raw = a.tobytes()",
                "    print(len(raw), raw[0], raw[1])",
                "    print(np.fromlist([7, 8], 'int64').tolist())",
                "    nested = np.array('float64', [1.0, 2.0, 3.0, 4.0]).reshape([2, 2]).tolist()",
                "    print(nested[0], nested[1])",
                "    return 0",
            ]
        ),
    )
    assert got == "24 1 0\n[7, 8]\n[1.0, 2.0] [3.0, 4.0]\n"


# ---------------------------------------------------------------------
# concatenate and stack
# ---------------------------------------------------------------------


def test_concatenate_and_stack(tmp_path: Path) -> None:
    got = _run(
        tmp_path,
        "nd_join.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('float64', [1.0, 2.0])",
                "    b = np.array('float64', [3.0])",
                "    print(np.concatenate([a, b]).tolist())",
                "    c2 = np.array('float64', [3.0, 4.0])",
                "    print(np.stack([a, c2]).tolist(), np.stack([a, c2]).shape)",
                "    m1 = np.array('int64', [1, 2]).reshape([1, 2])",
                "    m2 = np.array('int64', [3, 4]).reshape([1, 2])",
                "    print(np.concatenate([m1, m2]).tolist())",
                "    return 0",
            ]
        ),
    )
    assert got == "[1.0, 2.0, 3.0]\n[[1.0, 2.0], [3.0, 4.0]] [2, 2]\n[[1, 2], [3, 4]]\n"


# ---------------------------------------------------------------------
# the PS2001 fence: every name outside the subset refuses
# ---------------------------------------------------------------------


def test_fences_name_the_module() -> None:
    cases = [
        ("import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n    return np.dot(1, 2)", "PS2001", "pyscriptc_ndarray.dot"),
        ("import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n    return np.linalg.norm(1)", "PS2001", "pyscriptc_ndarray.linalg"),
        (
            "import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n    a = np.zeros([2])\n    return a.argmax()\n",
            None,
            None,
        ),  # in the subset, must not fence
    ]
    for source, code, message in cases:
        if code is None:
            mod = lower_source(source)
            assert not mod["fences"], mod["fences"]
            continue
        _fence(source, code, message)


def test_fences_method_outside_subset() -> None:
    _fence(
        "import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n    a = np.zeros([2])\n    return a.argsort()\n",
        "PS2001",
        "pyscriptc_ndarray",
    )


def test_fences_outside_dtype() -> None:
    _fence(
        "import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n    a = np.zeros([2], dtype='int16')\n    return 0\n",
        "PS2003",
        "int16",
    )


def test_fences_broadcast_shape_mismatch_is_runtime_not_compile() -> None:
    # M1 keeps broadcasting out; the compile accepts same-shaped arrays and
    # the runtime raises when the shapes differ, never a silent approximation.
    mod = lower_source(
        "import pyscriptc_ndarray as np\n\n\ndef run() -> int:\n"
        "    a = np.array('int64', [1, 2, 3])\n"
        "    b = np.array('int64', [1, 2])\n"
        "    return (a + b)[0]\n"
    )
    assert not mod["fences"], mod["fences"]


# ---------------------------------------------------------------------
# the numpy value-for-value fixture (the acceptance criterion)
# ---------------------------------------------------------------------


def test_fixture_matches_numpy_values(tmp_path: Path) -> None:
    """The closed subset against real numpy, value for value.

    Skipped when numpy is not installed on the host: the fixture is the
    acceptance criterion, the unit cases above are the always-on floor.
    """
    numpy = pytest.importorskip("numpy")
    a = numpy.array([1.0, 2.0, 3.0, 4.0])
    got = _run(
        tmp_path,
        "nd_fixture.py",
        "\n".join(
            [
                "import pyscriptc_ndarray as np",
                "",
                "def run() -> int:",
                "    a = np.array('float64', [1.0, 2.0, 3.0, 4.0])",
                "    print(a.tolist())",
                "    print((a + a).tolist())",
                "    print((a * 2).tolist())",
                "    print((a / 2).tolist())",
                "    print((a > 2).tolist())",
                "    print(a.sum(), a.min(), a.max(), a.mean(), a.argmax())",
                f'    print(f"{{a.std():.12f}} {{a.var():.12f}}")',
                "    print(a.reshape([2, 2]).tolist())",
                "    print(a.reshape([2, 2]).transpose().tolist())",
                "    print(a[a > 2].tolist())",
                "    print(np.concatenate([a, np.array('float64', [5.0])]).tolist())",
                "    return 0",
            ]
        ),
    )
    vals = [1.0, 2.0, 3.0, 4.0]
    mean = 2.5
    var = float(numpy.var(a))
    expect = (
        f"{[float(v) for v in a.tolist()]}\n".replace("[", "[").replace("]", "]")
        + f"{[float(v) for v in (a + a).tolist()]}\n"
        + f"{[float(v) for v in (a * 2).tolist()]}\n"
        + f"{[float(v) for v in (a / 2).tolist()]}\n"
        + f"{[bool(v) for v in (a > 2).tolist()]}\n"
        + f"{float(a.sum())} {float(a.min())} {float(a.max())} {float(a.mean())} {int(a.argmax())}\n"
        + f"{float(numpy.std(a)):.12f} {var:.12f}\n"
        + "[[1.0, 2.0], [3.0, 4.0]]\n"
        + "[[1.0, 3.0], [2.0, 4.0]]\n"
        + "[3.0, 4.0]\n"
        + "[1.0, 2.0, 3.0, 4.0, 5.0]\n"
    )
    assert got == expect, got
