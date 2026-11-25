"""Issue #446 (O3b): container, iterator and context protocol dispatch.

The resolved surfaces (getitem, setitem, delitem, len, contains, the
iteration families and the with family) run against the CPython oracle; the
refusals the decision fences stay compile errors. Differential probe:
probes/differential/protocol_containers.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_getitem_setitem_len_contains(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "box.py",
        "\n".join(
            [
                "class Box:",
                "    items: list[int]",
                "    def __init__(self) -> None:",
                "        self.items = []",
                "    def __getitem__(self, i: int) -> int:",
                "        return self.items[i]",
                "    def __setitem__(self, i: int, v: int) -> None:",
                "        self.items[i] = v",
                "    def __delitem__(self, i: int) -> None:",
                "        self.items.pop(i)",
                "    def __len__(self) -> int:",
                "        return len(self.items)",
                "    def __contains__(self, v: int) -> bool:",
                "        i: int = 0",
                "        while i < len(self.items):",
                "            if self.items[i] == v:",
                "                return True",
                "            i = i + 1",
                "        return False",
                "def probe() -> int:",
                "    b: Box = Box()",
                "    b.items = [10, 20, 30]",
                "    print(b[0], b[2])",
                "    b[1] = 99",
                "    print(b[1])",
                "    print(len(b))",
                "    print(99 in b, 5 in b)",
                "    del b[0]",
                "    print(len(b), b[0])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_iter_factory_and_self_iterator(tmp_path: Path) -> None:
    # The __iter__ result's own static type decides the loop: a class with
    # __next__ drives it through __next__, a factory returns one.
    path = write_case(
        tmp_path,
        "iter.py",
        "\n".join(
            [
                "class Countdown:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __iter__(self) -> Countdown:",
                "        return self",
                "    def __next__(self) -> int:",
                "        if self.n <= 0:",
                "            raise StopIteration",
                "        self.n = self.n - 1",
                "        return self.n",
                "class Range:",
                "    lo: int",
                "    hi: int",
                "    def __init__(self, lo: int, hi: int) -> None:",
                "        self.lo = lo",
                "        self.hi = hi",
                "    def __iter__(self) -> Countdown:",
                "        return Countdown(self.hi - self.lo)",
                "def probe() -> int:",
                "    s: int = 0",
                "    for v in Countdown(3):",
                "        s = s + v",
                "    print(s)",
                "    total: int = 0",
                "    for v in Range(0, 4):",
                "        total = total + v",
                "    print(total)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_iter_consumers(tmp_path: Path) -> None:
    # list, any, all and sum drain a user iterable through the shared
    # resolver; the in fallback scans the resolved iterator.
    path = write_case(
        tmp_path,
        "consumers.py",
        "\n".join(
            [
                "class Countdown:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __iter__(self) -> Countdown:",
                "        return self",
                "    def __next__(self) -> int:",
                "        if self.n <= 0:",
                "            raise StopIteration",
                "        self.n = self.n - 1",
                "        return self.n",
                "def probe() -> int:",
                "    print(list(Countdown(2)))",
                "    print(any(Countdown(2)), all(Countdown(2)))",
                "    print(sum(Countdown(4)))",
                "    print(1 in Countdown(3), 9 in Countdown(3))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_enumerate_and_zip(tmp_path: Path) -> None:
    # enumerate and zip return the source with its element type now, not the
    # element alone.
    path = write_case(
        tmp_path,
        "enumzip.py",
        "\n".join(
            [
                "class Countdown:",
                "    n: int",
                "    def __init__(self, n: int) -> None:",
                "        self.n = n",
                "    def __iter__(self) -> Countdown:",
                "        return self",
                "    def __next__(self) -> int:",
                "        if self.n <= 0:",
                "            raise StopIteration",
                "        self.n = self.n - 1",
                "        return self.n",
                "def probe() -> int:",
                "    i: int",
                "    for i, v in enumerate(Countdown(3)):",
                "        print(i, v)",
                "    a: int",
                "    c: int",
                "    for a, c in zip(Countdown(3), Countdown(2)):",
                "        print(a, c)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_with_family(tmp_path: Path) -> None:
    # The __exit__ carrier: the class name (a string), the exception and the
    # traceback stand-in. The CPython spellings erase onto the slots.
    path = write_case(
        tmp_path,
        "guard.py",
        "\n".join(
            [
                "class Guard:",
                "    name: str",
                "    def __init__(self, name: str) -> None:",
                "        self.name = name",
                "    def __enter__(self) -> str:",
                "        print('enter', self.name)",
                "        return self.name",
                "    def __exit__(",
                "        self,",
                "        et: type[BaseException] | None,",
                "        ev: BaseException | None,",
                "        tb: TracebackType | None,",
                "    ) -> bool:",
                "        print('exit', self.name)",
                "        return False",
                "def probe() -> int:",
                "    with Guard('g') as name:",
                "        print('body', name)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_protocol_containers_len_negative_is_valueerror(tmp_path: Path) -> None:
    # CPython raises ValueError("__len__() should return >= 0") for a
    # negative result; the lowering performs the same check.
    path = write_case(
        tmp_path,
        "neglen.py",
        "\n".join(
            [
                "class C:",
                "    n: int",
                "    def __init__(self) -> None:",
                "        self.n = 0",
                "    def __len__(self) -> int:",
                "        return -1",
                "def f() -> int:",
                "    c: C = C()",
                "    print(len(c))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode != 0, got.stdout
    assert "__len__() should return >= 0" in got.stderr


def test_protocol_containers_fences(tmp_path: Path) -> None:
    cases = [
        # A class without __getitem__ keeps the old subscript fence.
        (
            "nogetitem.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    print(c[0])",
                    "    return 0",
                    "",
                ]
            ),
            "subscript is lowered for list, dict, str, and bytes",
        ),
        # A class without __setitem__ has no item assignment.
        (
            "nosetitem.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    c[0] = 2",
                    "    return 0",
                    "",
                ]
            ),
            "has no item assignment",
        ),
        # del of a subscript stays refused when the class has no
        # __delitem__ definer.
        (
            "nodelitem.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    del c[0]",
                    "    return 0",
                    "",
                ]
            ),
            "del of a subscript is not lowered",
        ),
        # len on a class without __len__ keeps the old fence.
        (
            "nolen.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    print(len(c))",
                    "    return 0",
                    "",
                ]
            ),
            "len is not lowered for these arguments",
        ),
        # in on a class with neither __contains__ nor __iter__ names both.
        (
            "nocontains.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    print(1 in c)",
                    "    return 0",
                    "",
                ]
            ),
            "in on C needs __contains__ or __iter__",
        ),
        # A for loop over a class without __iter__ keeps the old fence.
        (
            "noiter.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    for x in c:",
                    "        print(x)",
                    "    return 0",
                    "",
                ]
            ),
            "for iterates over a list or range",
        ),
        # A __setitem__ that does not return None is refused.
        (
            "badsetitem.py",
            "\n".join(
                [
                    "class C:",
                    "    items: list[int]",
                    "    def __init__(self) -> None:",
                    "        self.items = []",
                    "    def __setitem__(self, i: int, v: int) -> int:",
                    "        return v",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    c[0] = 2",
                    "    return 0",
                    "",
                ]
            ),
            "C.__setitem__ returns int, not None",
        ),
        # A class without __exit__ is not a context manager.
        (
            "noexit.py",
            "\n".join(
                [
                    "class C:",
                    "    n: int",
                    "    def __init__(self) -> None:",
                    "        self.n = 0",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    with c:",
                    "        print('x')",
                    "    return 0",
                    "",
                ]
            ),
            "with is lowered for contextlib.suppress",
        ),
        # A missing __exit__ definer names it.
        (
            "noexitmethod.py",
            "\n".join(
                [
                    "class C:",
                    "    n: int",
                    "    def __init__(self) -> None:",
                    "        self.n = 0",
                    "    def __enter__(self) -> int:",
                    "        return self.n",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    with c:",
                    "        print('x')",
                    "    return 0",
                    "",
                ]
            ),
            "C.__exit__ is not a method",
        ),
        # An __exit__ that is not in the CPython spellings is refused.
        (
            "badexit.py",
            "\n".join(
                [
                    "class C:",
                    "    n: int",
                    "    def __init__(self) -> None:",
                    "        self.n = 0",
                    "    def __enter__(self) -> str:",
                    "        return 'x'",
                    "    def __exit__(self, et: str, ev: str, tb: str) -> bool:",
                    "        return False",
                    "def f() -> int:",
                    "    c: C = C()",
                    "    with c:",
                    "        print('x')",
                    "    return 0",
                    "",
                ]
            ),
            "C.__exit__ takes (Optional[str], Optional[Exception], Optional[str])",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
