"""Issue #412: `collections.abc` in annotations and the common TypeVar shapes.

`collections.abc` is annotation-only like `typing`: its abstract containers
erase to the concrete container they describe (`Mapping[K, V]` is
`dict[K, V]`, `MutableSequence[T]` and `Collection[T]` are `list[T]`,
`AbstractSet[T]` and `MutableSet[T]` are `set[T]`), every import spelling
binds (`from`, `import collections.abc`, an alias, a `TYPE_CHECKING`
guard), and a name used as a value keeps the PS2002 typing-name fence.
TypeVars cover `T | None` in a generic signature or class and `Mapping[K, V]`
with a TypeVar key, both monomorphized per call site.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[tuple[str, str]]:
    return [(fence["code"], fence["message"]) for fence in lower_source(source, "case.py")["fences"]]


_ABC = """
import collections
import collections.abc
import collections.abc as cabc
from collections.abc import Collection, Mapping, MutableMapping, MutableSequence, MutableSet, Sequence
from collections.abc import Set as AbstractSet


def head(xs: collections.abc.Sequence[int]) -> int:
    return xs[0]


def tail(xs: cabc.Sequence[str]) -> str:
    return xs[-1]


def total(m: Mapping[str, int]) -> int:
    s: int = 0
    for k in m:
        s += m[k]
    return s


def bump(m: MutableMapping[str, int], key: str) -> None:
    m[key] = m.get(key, 0) + 1


def grow(xs: MutableSequence[int], v: int) -> None:
    xs.append(v)


def size(xs: Collection[int]) -> int:
    return len(xs)


def has(s: AbstractSet[int], v: int) -> bool:
    return v in s


def add(s: MutableSet[str], v: str) -> int:
    s.add(v)
    return len(s)


def probe() -> str:
    nums: list[int] = [4, 5, 6]
    words: list[str] = ["a", "b"]
    d: dict[str, int] = {"x": 1, "y": 2}
    bump(d, "x")
    grow(nums, 7)
    si: set[int] = {1, 2}
    ss: set[str] = set()
    dq: collections.deque[int] = collections.deque([1, 2])
    parts: list[str] = [str(head(nums)), tail(words), str(total(d)), str(size(nums))]
    parts.append(str(has(si, 2)) + str(has(si, 9)) + str(add(ss, "q")) + str(len(dq)))
    return " ".join(parts)
"""


def test_collections_abc_spellings_lower_clean() -> None:
    assert _fences(_ABC) == []


def test_collections_abc_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "abc_forms.py", _ABC)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_type_checking_guarded_abc_names_resolve() -> None:
    source = "\n".join(
        [
            "from typing import TYPE_CHECKING",
            "if TYPE_CHECKING:",
            "    from collections.abc import Callable, Mapping",
            "    from collections.abc import Set as AbstractSet",
            "def app(f: Callable[[int], int], v: int) -> int:",
            "    return f(v)",
            "def total(m: Mapping[str, int]) -> int:",
            "    return len(m)",
            "def has(s: AbstractSet[str], v: str) -> bool:",
            "    return v in s",
            "",
        ]
    )
    assert _fences(source) == []


def test_abc_name_as_value_keeps_the_typing_fence() -> None:
    fences = _fences("from collections.abc import Mapping\nprint(Mapping)\n")
    assert fences == [("PS2002", "Mapping is a typing name and is not a value")]


def test_user_class_named_like_an_abc_form_wins() -> None:
    source = "\n".join(
        [
            "from typing import TYPE_CHECKING",
            "if TYPE_CHECKING:",
            "    from collections.abc import Mapping",
            "class Mapping:",
            "    def __init__(self) -> None:",
            "        self.n: int = 2",
            "def size(m: Mapping) -> int:",
            "    return m.n",
            "",
        ]
    )
    assert _fences(source) == []


_TYPEVAR = """
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Generic, TypeVar

T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")
U = TypeVar("U", infer_variance=True)


def first(xs: Sequence[T]) -> T:
    return xs[0]


def opt_first(xs: list[T]) -> T | None:
    if not xs:
        return None
    return xs[0]


def or_else(x: T | None, d: T) -> T:
    if x is None:
        return d
    return x


def keys_of(m: Mapping[K, V]) -> list[K]:
    out: list[K] = []
    for k in m:
        out.append(k)
    return out


def get_or(m: dict[K, V], k: K, dflt: V) -> V:
    return m.get(k, dflt)


def apply(f: Callable[[T], U], x: T) -> U:
    return f(x)


def collect(xs: Iterable[T]) -> list[T]:
    out: list[T] = []
    for x in xs:
        out.append(x)
    return out


class Box(Generic[T]):
    def __init__(self, v: T | None) -> None:
        self.v: T | None = v

    def get(self, d: T) -> T:
        if self.v is None:
            return d
        return self.v


def sz(s: str) -> int:
    return len(s)


def maybe(flag: bool) -> int | None:
    if flag:
        return 4
    return None


def probe() -> str:
    a: list[int] = [3, 4]
    b: list[str] = ["x", "y"]
    e: list[int] = []
    d: dict[str, int] = {"a": 1}
    parts: list[str] = [str(first(a)), first(b), str(opt_first(e) is None)]
    s = opt_first(b)
    if s is not None:
        parts.append(s)
    parts.append(str(or_else(maybe(False), 1)) + str(or_else(maybe(True), 1)) + or_else(None, "n"))
    parts.append(",".join(keys_of(d)) + str(get_or(d, "z", 9)) + str(apply(sz, "hello")) + str(collect([1.5])[0]))
    bx: Box[str] = Box("q")
    parts.append(bx.get("z"))
    return " ".join(parts)
"""


def test_typevar_common_shapes_lower_clean() -> None:
    assert _fences(_TYPEVAR) == []


def test_typevar_common_shapes_match_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "typevar_shapes.py", _TYPEVAR)
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_typevar_dict_key_outside_int_or_str_fences() -> None:
    source = "\n".join(
        [
            "from typing import TypeVar",
            "K = TypeVar('K')",
            "def size(m: dict[K, int]) -> int:",
            "    return len(m)",
            "def probe() -> int:",
            "    return size({1.5: 1})",
            "",
        ]
    )
    codes = [code for code, _ in _fences(source)]
    assert "PS2002" in codes or "PS1003" in codes


def test_typevar_default_keyword_stays_fenced() -> None:
    fences = _fences("from typing import TypeVar\nD = TypeVar('D', default=int)\n")
    assert fences == [("PS2003", "TypeVar keyword default is not lowered")]
