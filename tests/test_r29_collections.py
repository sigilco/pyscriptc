"""Real-Python backlog R29: ``collections.Counter``/``defaultdict``/``namedtuple``.

Counter and defaultdict lower to the existing map type with a mark on the
dict type: `counter` makes `c[k]` read 0 on a miss, `defaultdict` makes
`d[k]` insert the zero of the annotated value type. Construction counts in
ps_coll.c (str buffer, list, dict) or validates the factory name
(list/dict/set/int/float/str) against the `defaultdict[K, V]` annotation.
`namedtuple` declares a synthetic tuple class at the assignment site; the
first `P(...)` call fixes the field types, and `p.x`, `p[i]`, `p == q`
reuse the tuple-class machinery. Each probe diffs stdout against the
CPython oracle.

Excluded: Counter keywords/elements/most_common/subtract/total/arithmetic,
`Counter.update` with an iterable (a mapping adds counts), defaultdict
lambdas and `default_factory` access, `d[k] += v` (augmented subscript is
fenced), namedtuple `rename`/`defaults`/`module` keywords, `p.f = v`
(read-only), `P._make`/`p._replace`/`p._asdict`/`p._fields`, len/iteration
over a namedtuple, and `print(p)` (a namedtuple prints `P(x=..., y=...)`
in CPython; the subset fences it rather than printing the tuple shape).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container
def test_r29_counter_str_and_missing_key(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "counter_str.py",
        "\n".join(
            [
                "from collections import Counter",
                "",
                "def run() -> int:",
                "    c: Counter[str] = Counter('aabca')",
                "    print(c['a'])",
                "    print(c['b'])",
                "    print(c['z'])",
                "    print(len(c))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_counter_list_dict_and_update(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "counter_maps.py",
        "\n".join(
            [
                "from collections import Counter",
                "",
                "def run() -> int:",
                "    c: Counter[int] = Counter([1, 2, 1, 3, 1])",
                "    print(c[1])",
                "    d: Counter[str] = Counter({'x': 4})",
                "    d.update({'x': 1, 'y': 2})",
                "    print(d['x'])",
                "    print(d['y'])",
                "    print('x' in d)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_counter_get_keys_items(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "counter_methods.py",
        "\n".join(
            [
                "from collections import Counter",
                "",
                "def run() -> int:",
                "    c: Counter[str] = Counter('bbac')",
                "    print(c.get('a', -1))",
                "    print(c.get('q', -1))",
                "    print(c.pop('b'))",
                "    print(c.pop('b', 7))",
                "    total: int = 0",
                "    for k in c.keys():",
                "        total += c[k]",
                "    print(total)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_defaultdict_list_and_int(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "defaultdict_basic.py",
        "\n".join(
            [
                "from collections import defaultdict",
                "",
                "def run() -> int:",
                "    d: defaultdict[str, list[int]] = defaultdict(list)",
                "    d['x'].append(1)",
                "    d['x'].append(2)",
                "    d['x'].append(3)",
                "    print(len(d['x']))",
                "    print(d['x'][0])",
                "    print(len(d['y']))",
                "    print(len(d))",
                "    n: defaultdict[str, int] = defaultdict(int)",
                "    n['k'] = n['k'] + 5",
                "    print(n['k'])",
                "    print(n['missing'])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_defaultdict_dict_set_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "defaultdict_factories.py",
        "\n".join(
            [
                "from collections import defaultdict",
                "",
                "def run() -> int:",
                "    d: defaultdict[str, dict[str, int]] = defaultdict(dict)",
                "    d['a']['b'] = 3",
                "    print(d['a']['b'])",
                "    s: defaultdict[str, set[int]] = defaultdict(set)",
                "    s['k'].add(7)",
                "    print(len(s['k']))",
                "    t: defaultdict[str, str] = defaultdict(str)",
                "    print(t['e'] == '')",
                "    f: defaultdict[str, float] = defaultdict(float)",
                "    print(f['x'])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_namedtuple_fields_and_subscript(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "namedtuple_basic.py",
        "\n".join(
            [
                "from collections import namedtuple",
                "",
                "P = namedtuple('P', 'x y')",
                "Q = namedtuple('Q', ['name', 'score'])",
                "",
                "def run() -> int:",
                "    p: P = P(1, 2)",
                "    print(p.x)",
                "    print(p.y)",
                "    print(p[0])",
                "    print(p[1])",
                "    q: Q = Q('ann', 9)",
                "    print(q.name)",
                "    print(q[1])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


@pytest.mark.linux_container
def test_r29_namedtuple_kwargs_eq_and_scope(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "namedtuple_calls.py",
        "\n".join(
            [
                "import collections",
                "",
                "P = collections.namedtuple('P', 'x y')",
                "",
                "def make(a: int) -> P:",
                "    return P(a, a + 1)",
                "",
                "def run() -> int:",
                "    p: P = P(x=3, y=4)",
                "    print(p.x + p.y)",
                "    print(p == P(3, 4))",
                "    print(p == P(0, 0))",
                "    m: P = make(10)",
                "    print(m.y)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_namedtuple_construct_partial_failure_does_not_crash(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nt_crash.py",
        "\n".join(
            [
                "from collections import namedtuple",
                "",
                "TableFormat = namedtuple('TableFormat', ['lineabove', 'linebelowheader', 'linebetweenrows'])",
                "",
                "def make_table() -> None:",
                "    t1 = TableFormat('a', 'b', None)",
                "    t2 = TableFormat('x', 'y', 'z')",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--skip-check", "--emit", "ir", "-o", str(tmp_path / "nt.ir.json"))
    assert got.returncode == 3
    assert "TableFormat() field linebetweenrows is none" in got.stderr
