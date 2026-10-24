"""Generator frames (coro.py): CPython-oracle runs, fences, and IR shape."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.coro import EXC, STATE, STOP, YIELD_MARKER
from pyscriptc.ir import validate_module
from pyscriptc.lower import lower_source
from pyscriptc.package import lower_directory

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
ENV.pop("PYSC_CORO", None)

BASIC = """\
from typing import Generator, Iterator


def count(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def evens(xs: list[int]) -> Generator[int, None, None]:
    for x in xs:
        if x % 2 == 0:
            yield x
    yield -1


def chain(n: int) -> Iterator[int]:
    yield 100
    yield from count(n)
    yield from [7, 8]
    for k in range(n):
        if k == 1:
            continue
        if k == 3:
            return
        yield k * 10


def demo() -> None:
    total: int = 0
    for v in count(3):
        print(v)
        total += v
    for v in evens([1, 2, 3, 4]):
        print(v)
    for v in chain(5):
        print("c", v)
    print(total)


demo()
"""

SHAPES = """\
from typing import Iterator


def words(text: str) -> Iterator[str]:
    for w in text.split(" "):
        if w == "":
            continue
        yield w.upper()


def pairs(n: int) -> Iterator[tuple[int, str]]:
    i: int = 0
    while True:
        if i >= n:
            break
        if i % 2 == 0:
            yield (i, "even")
        else:
            yield (i, "odd")
        i += 1


def halves(xs: list[float]) -> Iterator[float]:
    for x in xs:
        yield x / 2


def nested(n: int) -> Iterator[int]:
    for i in range(n):
        j: int = 0
        while j < i:
            yield i * 10 + j
            j += 1
        yield -i


def empty() -> Iterator[int]:
    return
    yield 1


def walk(depth: int) -> Iterator[int]:
    yield depth
    if depth > 0:
        yield from walk(depth - 1)
        yield from walk(depth - 1)


def demo() -> None:
    for w in words("a  bc def"):
        print(w)
    for p in pairs(3):
        k, label = p
        print(k, label)
    for h in halves([1.0, 3.0]):
        print(h)
    for v in nested(3):
        print(v)
    for v in empty():
        print("never", v)
    out: list[int] = []
    for v in walk(2):
        out.append(v)
    print(len(out), sum(out))
    for v in nested(4):
        if v == 20:
            break
        print("b", v)


demo()
"""

FENCED = """\
from typing import Generator, Iterator


def sends() -> Generator[int, int, None]:
    yield 1


def expr_yield() -> Iterator[int]:
    x: int = 0
    print((yield x))


def bare_yield() -> Iterator[int]:
    yield


def closure(n: int) -> Iterator[int]:
    def inner() -> int:
        return n

    yield inner()
"""


def _run_both(tmp_path: Path, name: str, source: str) -> tuple[str, str]:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True).stdout
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "bin",
            "-o",
            str(tmp_path / name),
            "--run",
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    return built.stdout, expected


@pytest.mark.parametrize(("name", "source"), [("basic", BASIC), ("shapes", SHAPES)])
def test_generator_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_generator_ir_is_plain_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module = lower_source(BASIC, "basic.py")
    assert module["fences"] == []
    validate_module(module)
    assert YIELD_MARKER not in json.dumps(module)
    frames = {cls["name"]: cls for cls in module["classes"] if cls["name"].startswith("gen.")}
    assert set(frames) == {"gen.count", "gen.evens", "gen.chain"}
    for frame in frames.values():
        assert {m["name"] for m in frame["methods"]} == {"__iter__", "__next__", "close"}
        assert frame["fields"][0]["name"] == ".ps_state"


def test_module_without_generators_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    source = "def f(n: int) -> int:\n    return n + 1\n\n\nprint(f(2))\n"
    monkeypatch.setenv("PYSC_CORO", "0")
    off = lower_source(source, "plain.py")
    monkeypatch.delenv("PYSC_CORO")
    assert lower_source(source, "plain.py") == off


def test_unsupported_shapes_fence_precisely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYSC_CORO", raising=False)
    module = lower_source(FENCED, "fenced.py")
    got = sorted((f["span"]["line"], f["message"]) for f in module["fences"])
    assert got == [
        (10, "yield as an expression (send) is not lowered yet"),
        (14, "a bare yield needs an Optional element type"),
        (18, "a nested def or class in a resumable is not lowered yet"),
    ]
    assert {f["code"] for f in module["fences"]} == {"PS2002"}


def test_kill_switch_restores_old_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_CORO", "0")
    module = lower_source(BASIC, "basic.py")
    assert any(f["message"] == "yield is not lowered" for f in module["fences"])


# --- Package units and methods (issue #165) ---------------------------------

METHOD = """\
from typing import Iterator


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def upto(self) -> Iterator[int]:
        i: int = 0
        while i < self.n:
            yield i
            i += 1


def go() -> int:
    c: Counter = Counter(4)
    s: int = 0
    for v in c.upto():
        s += v
    return s


print(go())
"""

PKG_MOD = """\
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def count(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def total(xs: Sequence[int]) -> int:
    s: int = 0
    for x in xs:
        s += x
    return s
"""

PKG_USER = """\
from .mod import count


def run() -> int:
    s: int = 0
    n: int = 0
    for v in count(5):
        if n == 4:
            break
        s += v
        n += 1
    return s
"""


def test_generator_method_matches_cpython(tmp_path: Path) -> None:
    got, expected = _run_both(tmp_path, "method", METHOD)
    assert got == expected == "6\n"


def test_generator_method_ir_is_plain_classes() -> None:
    module = lower_source(METHOD, "method.py")
    assert module["fences"] == []
    validate_module(module)
    frames = {cls["name"] for cls in module["classes"] if cls["name"].startswith("gen.")}
    assert frames == {"gen.Counter.upto"}
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.Counter.upto")
    assert {m["name"] for m in frame["methods"]} == {"__iter__", "__next__", "close"}


def test_package_generator_lowers_and_cross_module_consumes(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "mod.py").write_text(PKG_MOD, encoding="utf-8")
    (root / "user.py").write_text(PKG_USER, encoding="utf-8")

    module, _ = lower_directory(root)
    assert module["fences"] == []
    validate_module(module)
    assert YIELD_MARKER not in json.dumps(module)

    names = {fn["name"] for fn in module["functions"]}
    assert "pkg.mod.count" in names
    assert "pkg.user.run" in names
    frames = {cls["name"] for cls in module["classes"] if cls["name"].startswith("gen.")}
    assert frames == {"gen.count"}
    # The consumer module binds the frame class through the export table
    # (its for-loop local `.ps_it_1` carries the `gen.count` shape).
    user = next(fn for fn in module["functions"] if fn["name"] == "pkg.user.run")
    assert any(local["type"] == {"kind": "class", "name": "gen.count"} for local in user["locals"])


def test_package_generator_runs_across_modules(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "mod.py").write_text(PKG_MOD, encoding="utf-8")
    (root / "user.py").write_text(PKG_USER, encoding="utf-8")
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(root),
            "--run",
            "--call",
            "pkg.user.run",
            "-o",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    assert built.stdout == "6\n"


def test_type_checking_iterator_annotation_is_not_a_fence() -> None:
    source = """\
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


def count(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def total(xs: Sequence[int]) -> int:
    s: int = 0
    for x in xs:
        s += x
    return s


def go() -> int:
    s: int = 0
    for v in count(3):
        s += v
    return s + total([1, 2])


print(go())
"""
    module = lower_source(source, "tc.py")
    assert module["fences"] == []
    assert YIELD_MARKER not in json.dumps(module)


# --- Generator protocol (#166) ----------------------------------------------

NEXT = """\
from typing import Iterator


def count(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def go() -> str:
    g: Iterator[int] = count(3)
    out: str = ""
    out += str(next(g))
    out += "," + str(next(g))
    out += "," + str(next(g, None))
    out += "," + str(next(g, None))
    h: Iterator[int] = count(1)
    out += "," + str(next(h, 42))
    try:
        next(h)
        out += ",noraise"
    except StopIteration:
        out += ",stop"
    return out


print(go())
"""

SEND = """\
from typing import Generator


def echo() -> Generator[int, int, None]:
    got: int = yield 1
    got2: int = yield got
    yield got2


def go() -> str:
    g: Generator[int, int, None] = echo()
    out: str = ""
    out += str(next(g))
    out += " " + str(g.send(21))
    out += " " + str(g.send(7))
    out += " " + str(next(g, -1))
    return out


print(go())
"""

# The generator frame carries close() and throw(), so the variables are
# annotated `Generator[int, None, None]`: that is the stock typeshed
# spelling for a generator, and the one that types close() and throw().
CLOSE_THROW = """\
from typing import Generator


def g() -> Generator[int, None, None]:
    yield 1
    yield 2


def go() -> str:
    a: Generator[int, None, None] = g()
    out: str = str(next(a))
    a.close()
    out += "," + str(next(a, None))
    a.close()
    b: Generator[int, None, None] = g()
    next(b)
    try:
        b.throw(ValueError)
    except ValueError:
        out += ",thrown"
    out += "," + str(next(b, None))
    return out


print(go())
"""

YIELD_FROM_CHAIN = """\
from typing import Iterator


def sub(n: int) -> Iterator[int]:
    i: int = 0
    while i < n:
        yield i
        i += 1


def chain(n: int) -> Iterator[int]:
    yield 100
    yield from sub(n)
    yield from [7, 8]


def go() -> str:
    out: str = ""
    for v in chain(3):
        out += str(v) + ","
    return out


print(go())
"""


@pytest.mark.parametrize(
    ("name", "source"), [("next", NEXT), ("send", SEND), ("close_throw", CLOSE_THROW), ("yf", YIELD_FROM_CHAIN)]
)
def test_generator_protocol_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert got == expected


def test_generator_fits_iterator_annotation() -> None:
    module = lower_source(NEXT, "next.py")
    assert module["fences"] == []
    validate_module(module)


def test_generator_send_ir_has_frame_surface() -> None:
    module = lower_source(SEND, "send.py")
    assert module["fences"] == []
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.echo")
    assert {m["name"] for m in frame["methods"]} == {"__iter__", "__next__", "close", "send"}
    assert any(field["name"] == ".ps_send" for field in frame["fields"])
    assert YIELD_MARKER not in json.dumps(module)


def test_generator_close_ir_has_method() -> None:
    module = lower_source(CLOSE_THROW, "ct.py")
    assert module["fences"] == []
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.g")
    assert "close" in {m["name"] for m in frame["methods"]}
    assert YIELD_MARKER not in json.dumps(module)


def test_generator_typed_send_annotation_lowers() -> None:
    source = """\
from typing import Generator


def echo() -> Generator[int, int, None]:
    got: int = yield 1
    yield got


def go() -> int:
    g: Generator[int, int, None] = echo()
    return g.send(0) + g.send(5)
"""
    module = lower_source(source, "gs.py")
    assert module["fences"] == []
    validate_module(module)


CONTEXT_MANAGER = """\
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def track(name: str) -> Iterator[str]:
    print("open", name)
    yield name.upper()
    print("close", name)


def main() -> None:
    with track("a") as value:
        print("body", value)
    with track("b") as value:
        print("body", value)


main()
"""

CONTEXT_MANAGER_RAISES = """\
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def track(name: str) -> Iterator[str]:
    print("open", name)
    yield name.upper()
    print("close", name)


def main() -> None:
    try:
        with track("a") as value:
            print("body", value)
            raise ValueError("boom")
    except ValueError as exc:
        print("caught", str(exc))
    with track("b") as value:
        print("body", value)


main()
"""

CONTEXT_MANAGER_METHOD = """\
from contextlib import contextmanager
from typing import Iterator


class Holder:
    base: int

    def __init__(self, base: int) -> None:
        self.base = base

    @contextmanager
    def scope(self, extra: int) -> Iterator[int]:
        print("open", self.base)
        yield self.base + extra
        print("close", self.base)


def main() -> None:
    holder: Holder = Holder(3)
    with holder.scope(5) as value:
        print("body", value)


main()
"""


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("cm_plain", CONTEXT_MANAGER),
        ("cm_raises", CONTEXT_MANAGER_RAISES),
        ("cm_method", CONTEXT_MANAGER_METHOD),
    ],
)
def test_contextmanager_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_contextmanager_ir_has_manager_class() -> None:
    module = lower_source(CONTEXT_MANAGER, "cm.py")
    assert module["fences"] == []
    validate_module(module)
    manager = next(cls for cls in module["classes"] if cls["name"] == "cm.track")
    assert {m["name"] for m in manager["methods"]} == {"__enter__", "__exit__"}
    # The manager holds the generator frame and the yielded value.
    assert [field["name"] for field in manager["fields"]] == [".ps_gen", ".ps_val"]
    # The outward type of `track` is the manager, not the generator frame.
    wrapper = next(fn for fn in module["functions"] if fn["name"].endswith("track"))
    assert wrapper["returnType"]["name"] == "cm.track"
    assert YIELD_MARKER not in json.dumps(module)


def test_contextmanager_method_ir_has_manager_class() -> None:
    module = lower_source(CONTEXT_MANAGER_METHOD, "cmm.py")
    assert module["fences"] == []
    validate_module(module)
    manager = next(cls for cls in module["classes"] if cls["name"] == "cm.gen.Holder.scope")
    assert {m["name"] for m in manager["methods"]} == {"__enter__", "__exit__"}


def test_contextmanager_without_yield_is_not_a_manager() -> None:
    source = """\
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def track(name: str) -> Iterator[str]:
    print(name)
    yield name
"""
    # A generator that yields once still lowers; the fence that matters is
    # the decorator, not the yield count.
    module = lower_source(source, "cm1.py")
    assert module["fences"] == []


def test_contextmanager_kill_switch_restores_the_old_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_CORO", "0")
    module = lower_source(CONTEXT_MANAGER, "cm.py")
    messages = [fence["message"] for fence in module["fences"]]
    assert any("decorators are not lowered" in message for message in messages)


def test_contextmanager_decorator_must_resolve_to_the_stdlib() -> None:
    source = """\
from typing import Iterator


def contextmanager(fn: object) -> object:
    return fn


@contextmanager
def track(name: str) -> Iterator[str]:
    yield name
"""
    module = lower_source(source, "cm_local.py")
    messages = [fence["message"] for fence in module["fences"]]
    assert any("decorators are not lowered" in message for message in messages)


# --- try/except/finally (issue #164) ----------------------------------------

TRY_FINALLY = """\
from typing import Iterator


def guarded() -> Iterator[int]:
    try:
        yield 1
        yield 2
    finally:
        print("fin")
    yield 3


def guarded_loop(n: int) -> Iterator[int]:
    for i in range(n):
        try:
            yield i
        finally:
            print("fin", i)


def demo() -> None:
    for v in guarded():
        print("g", v)
    for v in guarded_loop(3):
        print("l", v)


demo()
"""

TRY_EXCEPT = """\
from typing import Iterator


def resume_in_handler() -> Iterator[int]:
    try:
        raise ValueError("boom")
    except ValueError as e:
        print("caught", str(e))
        yield 1
        yield 2
    yield 3


def bare_raise_after_resume() -> Iterator[int]:
    try:
        raise ValueError("bang")
    except ValueError:
        yield 1
        raise
    yield 99


def pick() -> Iterator[int]:
    try:
        if True:
            raise TypeError("t")
        raise ValueError("v")
    except ValueError:
        print("hv")
        yield 1
    except TypeError:
        print("ht")
        yield 2
    yield 3


def demo() -> None:
    for v in resume_in_handler():
        print("r", v)
    try:
        for v in bare_raise_after_resume():
            print("b", v)
    except ValueError as e:
        print("outer", str(e))
    for v in pick():
        print("p", v)


demo()
"""

TRY_ELSE = """\
from typing import Iterator


def with_else(n: int) -> Iterator[int]:
    try:
        yield n
    except ValueError:
        print("never")
        yield -1
    else:
        print("else ran")
        yield n + 1
    finally:
        print("fin")


def raise_first(n: int) -> Iterator[int]:
    try:
        yield n
        raise ValueError("mid")
    except ValueError:
        yield n + 10
    finally:
        print("fin", n)


def demo() -> None:
    for v in with_else(5):
        print("w", v)
    try:
        for v in raise_first(1):
            print("r", v)
    except ValueError as e:
        print("caught", str(e))


demo()
"""

TRY_NESTED = """\
from typing import Iterator


def nested() -> Iterator[int]:
    try:
        try:
            yield 1
        finally:
            print("inner fin")
        yield 2
    finally:
        print("outer fin")


def handler_then_finally() -> Iterator[int]:
    try:
        raise ValueError("x")
    except ValueError:
        try:
            yield 1
        finally:
            print("h fin")
    finally:
        print("e fin")


def demo() -> None:
    for v in nested():
        print("n", v)
    for v in handler_then_finally():
        print("h", v)


demo()
"""


@pytest.mark.parametrize(
    ("name", "source"),
    [("try_finally", TRY_FINALLY), ("try_except", TRY_EXCEPT), ("try_else", TRY_ELSE), ("try_nested", TRY_NESTED)],
)
def test_generator_try_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_generator_try_ir_is_plain_classes() -> None:
    module = lower_source(TRY_FINALLY, "tryfin.py")
    assert module["fences"] == []
    validate_module(module)
    assert YIELD_MARKER not in json.dumps(module)
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.guarded")
    assert frame["fields"][0]["name"] == STATE


def test_suspending_handler_frame_keeps_the_exception() -> None:
    module = lower_source(TRY_EXCEPT, "tryexc.py")
    assert module["fences"] == []
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.resume_in_handler")
    assert any(field["name"] == EXC for field in frame["fields"])
    validate_module(module)


def test_plain_handler_frame_has_no_exception_field() -> None:
    module = lower_source(TRY_FINALLY, "tryfin.py")
    for cls in module["classes"]:
        if cls["name"].startswith("gen."):
            assert not any(field["name"] == EXC for field in cls["fields"])


# --- Generator statement shapes (issue #433) --------------------------------

# A bare `yield` hands None to the consumer. `Iterator[None]` is None itself,
# an optional element boxes it.
BARE_YIELD = """\
from typing import Iterator, Optional


def none_gen() -> Iterator[None]:
    yield


def opt_gen() -> Iterator[Optional[int]]:
    yield
    yield 1


def demo() -> str:
    out: str = ""
    for v in none_gen():
        out += str(v is None)
    n: int = 0
    for w in opt_gen():
        n += 1
        if w is not None:
            out += " " + str(w)
    out += " " + str(n)
    return out


print(demo())
"""

# `return v` in a generator carries the value (CPython's StopIteration.value).
RETURN_VALUE = """\
from typing import Generator


def g(n: int) -> Generator[int, None, int]:
    yield 1
    if n > 0:
        yield 2
        return n * 10
    return -1


def demo() -> str:
    out: str = ""
    for v in g(1):
        out += str(v)
    for v in g(0):
        out += " " + str(v)
    return out


print(demo())
"""

# The full annotation forms: `Generator[T]`, `Generator[T, S, R]` and
# `Iterable[T]`.
GENERATOR_FORMS = """\
from typing import Generator, Iterable, Iterator


def one() -> Generator[int]:
    yield 1


def three() -> Generator[int, str, bool]:
    got: str = yield 1
    yield len(got)


def iterable() -> Iterable[int]:
    yield 1


def plain() -> Iterator[int]:
    yield 1


def demo() -> str:
    out: str = ""
    for v in one():
        out += str(v)
    g: Generator[int, str, bool] = three()
    out += " " + str(next(g))
    out += " " + str(g.send("abc"))
    for v in iterable():
        out += " " + str(v)
    for v in plain():
        out += " " + str(v)
    return out


print(demo())
"""

# A `with` block in a generator. CPython suspends inside the block without
# running `__exit__`, and runs it when the block leaves.
YIELD_IN_WITH = """\
from typing import Iterator


class Ctx:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> str:
        print("enter", self.tag)
        return self.tag

    def __exit__(self, t: str | None, v: Exception | None, tb: str | None) -> bool:
        print("exit", self.tag)
        return False


def g() -> Iterator[str]:
    with Ctx("a") as x:
        yield x
        with Ctx("b") as y:
            yield x + y
        yield "after"


def demo() -> None:
    for v in g():
        print("v", v)


demo()
"""


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("bare_yield", BARE_YIELD),
        ("return_value", RETURN_VALUE),
        ("generator_forms", GENERATOR_FORMS),
        ("yield_in_with", YIELD_IN_WITH),
    ],
)
def test_generator_shapes_match_cpython(tmp_path: Path, name: str, source: str) -> None:
    got, expected = _run_both(tmp_path, name, source)
    assert expected
    assert got == expected


def test_bare_yield_needs_an_optional_element_type() -> None:
    module = lower_source(
        "from typing import Iterator\n\n\ndef g() -> Iterator[int]:\n    yield\n",
        "bare.py",
    )
    assert [(f["code"], f["message"]) for f in module["fences"]] == [
        ("PS2002", "a bare yield needs an Optional element type")
    ]


def test_return_value_frame_carries_the_stop_field() -> None:
    module = lower_source(RETURN_VALUE, "ret.py")
    assert module["fences"] == []
    validate_module(module)
    frame = next(cls for cls in module["classes"] if cls["name"] == "gen.g")
    assert any(field["name"] == STOP for field in frame["fields"])


def test_generator_without_a_return_type_has_no_stop_field() -> None:
    module = lower_source(BASIC, "basic.py")
    for cls in module["classes"]:
        if cls["name"].startswith("gen."):
            assert not any(field["name"] == STOP for field in cls["fields"])


def test_send_type_drives_the_received_value() -> None:
    module = lower_source(
        "from typing import Generator\n\n\n"
        "def g() -> Generator[int, str, None]:\n"
        "    got: str = yield 1\n"
        "    yield len(got)\n",
        "sendty.py",
    )
    assert module["fences"] == []
    validate_module(module)


def test_send_value_must_match_the_annotation() -> None:
    module = lower_source(
        "from typing import Generator\n\n\ndef g() -> Generator[int, str, None]:\n    got: int = yield 1\n",
        "sendbad.py",
    )
    assert [(f["code"], f["message"]) for f in module["fences"]] == [
        ("PS1003", "the sent value is str, annotation says int")
    ]
