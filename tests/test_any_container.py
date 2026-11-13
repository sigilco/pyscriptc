"""Bridge-resolved Any containers (issue #782, option B).

`dict[str, Any]` and `list[Any]` annotations compile: the container shape
is known, the element slot is the unresolved `any` kind. A read out of the
container must prove its element type at the read site, from the read's
expected type (an annotated assignment target, a call parameter) or the
checker bridge; a read nothing proves fences with PS1107. A concrete
initializer satisfies the annotation without changing the element slot.
Bare `Any` and `object` in a non-container position still fence PS1104.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _case(tmp_path: Path, name: str, body: list[str]) -> str:
    source = "\n".join(
        ["from typing import Any", "", "def main() -> int:", *[f"    {line}" for line in body], "    return 0", ""]
    )
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")
    return got.stdout


def test_dict_any_annotated_read(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "any_dict_read.py",
        [
            "d: dict[str, Any] = {}",
            "d['k'] = 7",
            "v: int = d['k']",
            "print(v)",
        ],
    )
    assert out == "7\n0\n"


def test_dict_any_str_roundtrip(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "any_dict_str.py",
        [
            "s: str = 'ok'",
            "dd: dict[str, Any] = {}",
            "dd['s'] = s",
            "word: str = dd['s']",
            "print(word)",
        ],
    )
    assert out == "ok\n0\n"


def test_dict_any_concrete_init(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "any_dict_init.py",
        [
            "d: dict[str, Any] = {'a': 1}",
            "v: int = d['a']",
            "print(v)",
        ],
    )
    assert out == "1\n0\n"


def test_list_any_loop(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "any_list_loop.py",
        [
            "xs: list[Any] = [1, 2, 3]",
            "total = 0",
            "for x in xs:",
            "    total = total + x",
            "print(total)",
        ],
    )
    assert out == "6\n0\n"


def test_list_any_append(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "any_list_append.py",
        [
            "xs: list[Any] = []",
            "xs.append(5)",
            "xs.append(6)",
            "v: int = xs[1]",
            "print(v)",
        ],
    )
    assert out == "6\n0\n"


def test_unproven_read_fences_ps1107(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from typing import Any",
            "",
            "def main() -> None:",
            "    d: dict[str, Any] = {}",
            "    print(d['k'])",
            "",
        ]
    )
    path = write_case(tmp_path, "any_unproven.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1107" in got.stderr


def test_bare_any_still_fences_ps1104(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from typing import Any",
            "",
            "def f(x: Any) -> int:",
            "    return 0",
            "",
            "def main() -> int:",
            "    return f(1)",
            "",
        ]
    )
    path = write_case(tmp_path, "any_bare.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1104" in got.stderr


def test_object_still_fences_ps1104(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "def f(x: object) -> int:",
            "    return 0",
            "",
            "def main() -> int:",
            "    return f(1)",
            "",
        ]
    )
    path = write_case(tmp_path, "any_object.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1104" in got.stderr


def test_dict_any_parameter_lowers(tmp_path: Path) -> None:
    """Issue #817: `dict[str, Any]` in a parameter lowers like `Dict[str, Any]`."""
    source = "\n".join(
        [
            "from typing import Any",
            "",
            "def get(d: dict[str, Any], k: str) -> int:",
            "    v: int = d[k]",
            "    return v",
            "",
            "def main() -> int:",
            "    d: dict[str, Any] = {'n': 4}",
            "    return get(d, 'n')",
            "",
        ]
    )
    path = write_case(tmp_path, "any_dict_param.py", source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_dict_any_return_lowers(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from typing import Any",
            "",
            "def make() -> dict[str, Any]:",
            "    d: dict[str, Any] = {'m': 2}",
            "    return d",
            "",
            "def main() -> int:",
            "    v: int = make()['m']",
            "    return v",
            "",
        ]
    )
    path = write_case(tmp_path, "any_dict_ret.py", source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_dict_any_parameter_unproven_read_fences(tmp_path: Path) -> None:
    """The dict spelling keeps the Dict spelling's PS1107 proof duty."""
    source = "\n".join(
        [
            "from typing import Any",
            "",
            "def get(d: dict[str, Any], total: int) -> int:",
            "    total = total + d['missing']",
            "    return total",
            "",
            "def main() -> int:",
            "    return get({}, 0)",
            "",
        ]
    )
    path = write_case(tmp_path, "any_dict_param_unproven.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1107" in got.stderr


# Issue #483: the bare container names `list`, `dict` and `set` annotate as the
# container shape with `any` element slots — the same `any` kind the
# `dict[K, Any]`/`list[Any]` spellings carry. A local's slots pin from the
# body's mutations through the accumulated-type inference an unannotated
# `x = []` gets; a read nothing proves keeps its PS1107 fence. `tuple`
# keeps its PS1106 fence: its shape is its arity.


def test_bare_list_accumulates(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "bare_list_acc.py",
        [
            "out: list = []",
            "out.append(1)",
            "out.append(2)",
            "total = 0",
            "for x in out:",
            "    total = total + x",
            "print(total)",
        ],
    )
    assert out == "3\n0\n"


def test_bare_list_bare_decl(tmp_path: Path) -> None:
    # `xs: list` zero-binds like every bare `x: T` decl in the subset
    # (CPython leaves the name unbound instead), so there is no CPython
    # oracle to diff against: assert the compiled run.
    source = "\n".join(
        [
            "def main() -> int:",
            "    xs: list",
            "    xs.append(3)",
            "    xs.append(4)",
            "    print(xs[0] + xs[1])",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_list_decl.py", source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n0\n"


def test_bare_list_concrete_init(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "bare_list_init.py",
        [
            "xs: list = [1, 2]",
            "print(xs[1])",
        ],
    )
    assert out == "2\n0\n"


def test_bare_dict_counter(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "bare_dict_counter.py",
        [
            "words = ['a', 'b', 'a']",
            "seen: dict = {}",
            "for w in words:",
            "    seen[w] = seen.get(w, 0) + 1",
            "v: int = seen['a']",
            "print(v)",
        ],
    )
    assert out == "2\n0\n"


def test_bare_dict_int_key(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "bare_dict_intkey.py",
        [
            "d: dict = {}",
            "d[7] = 'x'",
            "v: str = d[7]",
            "print(v)",
        ],
    )
    assert out == "x\n0\n"


def test_bare_set_add(tmp_path: Path) -> None:
    out = _case(
        tmp_path,
        "bare_set_add.py",
        [
            "s: set = set()",
            "s.add(1)",
            "s.add(2)",
            "print(len(s))",
        ],
    )
    assert out == "2\n0\n"


def test_bare_list_return_lowers(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "def load(path: str) -> list:",
            "    out: list = []",
            "    out.append(path)",
            "    return out",
            "",
            "def main() -> int:",
            "    v: str = load('a')[0]",
            "    print(v)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_list_ret.py", source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_bare_list_parameter_append(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "def grow(xs: list) -> int:",
            "    xs.append(9)",
            "    return len(xs)",
            "",
            "def main() -> int:",
            "    return grow([1])",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_list_param.py", source)
    got = compile_run(tmp_path, path, "main")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "main")


def test_bare_list_unproven_read_fences(tmp_path: Path) -> None:
    """No mutation pins the element: the read keeps its named PS1107 fence."""
    source = "\n".join(
        [
            "def main() -> int:",
            "    out: list = []",
            "    print(out[0])",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_list_unproven.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1107" in got.stderr


def test_bare_list_parameter_unproven_read_fences(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "def first(xs: list) -> int:",
            "    return xs[0] + xs[1]",
            "",
            "def main() -> int:",
            "    return first([1])",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_list_param_read.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1107" in got.stderr


def test_bare_tuple_still_fences_ps1106(tmp_path: Path) -> None:
    """A bare `tuple` carries no arity, so its shape is not known."""
    source = "\n".join(
        [
            "def main() -> int:",
            "    t: tuple = ()",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "bare_tuple.py", source)
    got = compile_run(tmp_path, path, "main")
    assert "PS1106" in got.stderr
