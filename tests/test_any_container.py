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
