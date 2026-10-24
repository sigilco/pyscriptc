"""Issue #500: a local, parameter or return type binds from what proves it.

The rule the lowerer implements: inference binds only when lowering itself
proves the type. A literal, a display or comprehension of provable elements,
a call whose callee already signed, an empty display whose appends and item
writes agree, a typed default, and call sites that all agree are proofs.
Everything else keeps the PS1001 annotation fence with the refactor hint;
an annotated binding always keeps its annotation. Ambiguity is never a
guess: a bare None, a lambda, an empty display with no provable use and a
call that does not lower are named fences.

Every newly accepted program must match CPython byte for byte: the
differential cases below run the compiled binary and compare with the
CPython oracle, and the fence cases hold the refusals the rule keeps.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.coverage import family_of

_DIFFERENTIAL_CASES: dict[str, str] = {
    "int literal": "def probe() -> int:\n    total = 3\n    return total * 7\n",
    "float literal": "def probe() -> int:\n    ratio = 0.5\n    return round(ratio * 4)\n",
    "str literal": "def probe() -> int:\n    who = 'py'\n    return len(who + '!')\n",
    "list display": "def probe() -> int:\n    xs = [1, 2, 3]\n    return xs[0] + xs[2]\n",
    "dict display": "def probe() -> int:\n    d = {'a': 1, 'b': 2}\n    return d['a'] + d['b']\n",
    "set display": (
        "def probe() -> int:\n"
        "    s = {1, 2}\n"
        "    return len(s) * 10 + (1 if 1 in s else 0)\n"
    ),
    "list comp": "def probe() -> int:\n    xs = [v * 2 for v in [1, 2, 3]]\n    return xs[1]\n",
    "str method call": (
        "def probe() -> int:\n"
        "    tag = '-'.join(['a', 'b'])\n"
        "    return len(tag) * 10 + ord(tag[1])\n"
    ),
    "len call": "def probe() -> int:\n    n = len([1, 2, 3, 4])\n    return n * 2\n",
    "user call": (
        "def inner(v: int) -> int:\n"
        "    return v + 1\n"
        "\n"
        "def probe() -> int:\n"
        "    got = inner(4)\n"
        "    return got\n"
    ),
    "empty list appended": (
        "def probe() -> int:\n"
        "    xs = []\n"
        "    for i in range(4):\n"
        "        xs.append(i * i)\n"
        "    return xs[2]\n"
    ),
    "empty dict written": (
        "def probe() -> int:\n"
        "    counts = {}\n"
        "    for w in ['a', 'b', 'a']:\n"
        "        counts[w] = counts.get(w, 0) + 1\n"
        "    return counts['a'] * 10 + counts['b']\n"
    ),
    "display iterable append": (
        "def probe() -> int:\n"
        "    xs = []\n"
        "    for w in ['ab', 'cd']:\n"
        "        xs.append(len(w))\n"
        "    return xs[0] * 10 + xs[1]\n"
    ),
    "tuple unpack display": "def probe() -> int:\n    a, b = 1, 2\n    return a * 10 + b\n",
    "splitext unpack": (
        "import os\n"
        "\n"
        "def probe() -> int:\n"
        "    stem, ext = os.path.splitext('name.txt')\n"
        "    return len(stem) * 10 + len(ext)\n"
    ),
    "default param": (
        "def shout(msg, times=3):\n"
        "    return msg * times\n"
        "\n"
        "def probe() -> int:\n"
        "    return len(shout('ha'))\n"
    ),
    "default param call override": (
        "def shout(msg, times=3):\n"
        "    return msg * times\n"
        "\n"
        "def probe() -> int:\n"
        "    return len(shout('ha', 2))\n"
    ),
    "pinned param": (
        "def twice(v):\n"
        "    return v + v\n"
        "\n"
        "def probe() -> int:\n"
        "    return twice(21)\n"
    ),
    "pinned param unpack arg": (
        "import os\n"
        "\n"
        "def report(parts):\n"
        "    return len(parts[0]) + len(parts[1])\n"
        "\n"
        "def probe() -> int:\n"
        "    return report(os.path.splitext('a.txt'))\n"
    ),
    "return from body": (
        "def pick():\n"
        "    return 42\n"
        "\n"
        "def probe() -> int:\n"
        "    return pick() + 1\n"
    ),
    "return none body": (
        "def side():\n"
        "    print('ran')\n"
        "\n"
        "def probe() -> int:\n"
        "    side()\n"
        "    return 0\n"
    ),
    "return from pinned callee": (
        "def label(v):\n"
        "    return 'x' * v\n"
        "\n"
        "def probe() -> int:\n"
        "    return len(label(3))\n"
    ),
    "str repeat": (
        "def probe() -> int:\n"
        "    bar = 'ab' * 3\n"
        "    return len(bar) * 10 + (1 if bar == 'ababab' else 0)\n"
    ),
    "unpack local reuse": (
        "def probe() -> int:\n"
        "    stem, dot, ext = 'name.tar.gz'.rpartition('.')\n"
        "    return len(stem) * 100 + len(dot) * 10 + len(ext)\n"
    ),
}


@pytest.mark.parametrize("name", sorted(_DIFFERENTIAL_CASES))
def test_inferred_bindings_match_cpython(tmp_path: Path, name: str) -> None:
    path = write_case(tmp_path, "case.py", _DIFFERENTIAL_CASES[name])
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe"), name


def test_str_times_int_matches_cpython_runtime(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "repeat.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    a = 'xy' * 4",
                "    b = 3 * 'z'",
                "    c = b'q' * 2",
                "    zero = 'n' * 0",
                "    neg = 'n' * -2",
                "    return len(a) * 1000000 + len(b) * 1000 + len(c) * 10 + len(zero) + len(neg)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_inference_never_guesses(tmp_path: Path) -> None:
    """The ambiguous shapes keep their fence, byte-identical messages."""
    cases = [
        ("none_local.py", "v = None", "v needs an annotation before use"),
        ("lambda_local.py", "f = lambda x: x", "f needs an annotation before use"),
        ("empty_orphan.py", "xs = []", "xs needs an annotation before use"),
    ]
    for name, line, message in cases:
        source = "\n".join(["def probe() -> int:", f"    {line}", "    return 0", ""])
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "probe")
        assert got.returncode == 3, (name, got.stderr)
        assert message in got.stderr, (name, got.stderr)


def test_unprovable_callee_return_keeps_fence(tmp_path: Path) -> None:
    """A def whose return paths disagree proves nothing: the binding fences."""
    path = write_case(
        tmp_path,
        "unproven.py",
        "\n".join(
            [
                "def other(flag: bool):",
                "    if flag:",
                "        return 1",
                "    return 'no'",
                "",
                "def probe() -> int:",
                "    v = other(False)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "other needs a return annotation" in got.stderr
    assert "call to other is not lowered" in got.stderr


def test_param_without_default_or_calls_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nopin.py",
        "\n".join(
            [
                "def lonely(v):",
                "    return v",
                "",
                "def probe() -> int:",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "parameter v needs an annotation" in got.stderr


def test_return_paths_that_disagree_stay_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "split.py",
        "\n".join(
            [
                "def pick(flag: bool) -> int:",
                "    if flag:",
                "        return 1",
                "    return len('no')",
                "",
                "def other(flag: bool):",
                "    if flag:",
                "        return 1",
                "    return 'no'",
                "",
                "def probe() -> int:",
                "    other(False)",
                "    return pick(False)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "other needs a return annotation" in got.stderr


def test_annotated_bindings_keep_their_annotation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "annotated.py",
        "\n".join(
            [
                "def shrink(v: float) -> int:",
                "    return int(v / 2)",
                "",
                "def probe() -> int:",
                "    n: float = 4.0",
                "    return shrink(n)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_none_default_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "optdefault.py",
        "\n".join(
            [
                "def f(x=None):",
                "    return 0",
                "",
                "def probe() -> int:",
                "    return f()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "parameter x needs an annotation" in got.stderr


def test_pin_refuses_when_call_sites_disagree(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "mixed.py",
        "\n".join(
            [
                "def mix(v):",
                "    return 0",
                "",
                "def probe() -> int:",
                "    mix(1)",
                "    mix('a')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "parameter v needs an annotation" in got.stderr


def test_generator_return_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "gen.py",
        "\n".join(
            [
                "def g():",
                "    yield 1",
                "",
                "def probe() -> int:",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "g needs a return annotation" in got.stderr


def test_empty_display_without_use_stays_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "orphan.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    xs = []",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 3
    assert "xs needs an annotation before use" in got.stderr


def test_families_unchanged() -> None:
    assert family_of("PS1001", "x needs an annotation before use") == "local-inference"
    assert family_of("PS1001", "parameter v needs an annotation") == "param-inference"
    assert family_of("PS1001", "f needs a return annotation") == "return-inference"
