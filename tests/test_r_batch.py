"""Real-Python backlog R1-R5: cheap-batch lowering of method cores, print
kwargs, and scalar casts.

Each test compiles the backlog doc's acceptance shape inside a function,
runs the emitted binary, and diffs stdout against the CPython oracle
(`tests.test_gates.compile_run` / `oracle`). Case and ordering semantics
match CPython; `str` predicates stay ASCII/Latin-1 bytewise.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def test_r1_dict_core(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r1.py",
        "\n".join(
            [
                "def run() -> int:",
                '    d: dict[str, int] = {"a": 1, "b": 2}',
                '    print(d.get("a", 0))',
                '    print(d.get("zz", 7))',
                '    d.setdefault("c", 3)',
                '    print(d.get("c", 0))',
                '    v: int = d.pop("b", 0)',
                "    print(v)",
                "    for it in d.items():",
                "        print(it[0], it[1])",
                "    for x in d.values():",
                "        print(x)",
                '    d.update({"z": 9, "a": 5})',
                '    print(d.get("z", 0), d.get("a", 0))',
                '    del d["a"]',
                '    print(d.get("a", -1))',
                "    d.clear()",
                '    print(d.get("c", -1))',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r2_list_core(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r2.py",
        "\n".join(
            [
                "def run() -> int:",
                "    xs: list[int] = [3, 1]",
                "    xs.extend([2])",
                "    xs.sort()",
                "    print(xs[0], xs[1], xs[2])",
                "    xs += [5]",
                "    ys: list[int] = xs * 2",
                "    print(len(xs), len(ys))",
                "    xs.insert(0, 9)",
                "    print(xs.index(9), xs.count(5))",
                "    xs.remove(9)",
                "    w: int = xs.pop()",
                "    print(w, xs[0])",
                "    zs: list[int] = xs.copy()",
                "    zs.pop()",
                "    print(len(xs), len(zs))",
                "    fs: list[float] = [2.5, 0.5]",
                "    fs.sort(reverse=True)",
                "    print(fs[0], fs[1])",
                "    print(fs.index(0.5))",
                "    ss: list[str] = ['b', 'a']",
                "    ss.sort()",
                "    print(ss[0], ss[1], ss.index('b'), ss.count('a'))",
                "    xs.clear()",
                "    print(len(xs))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r3_str_tier2(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r3.py",
        "\n".join(
            [
                "def run() -> int:",
                '    s: str = "a,b,c"',
                '    print(s.count(","), s.rfind(","), s.index("b"))',
                '    tp: tuple[str, str, str] = s.partition(",")',
                "    print(tp[0], tp[1], tp[2])",
                '    rp: tuple[str, str, str] = "abc".rpartition("b")',
                "    print(rp[0], rp[1], rp[2])",
                '    sp: list[str] = s.split(",", 1)',
                "    print(sp[0], sp[1])",
                '    rs: list[str] = s.rsplit(",", 1)',
                "    print(rs[0], rs[1])",
                '    ws: list[str] = "  a b  c ".split()',
                "    print(len(ws), ws[2])",
                '    wr: list[str] = "a  b c ".rsplit()',
                "    print(wr[0], wr[1])",
                '    sl: list[str] = "x\\ny\\r\\nz".splitlines()',
                "    print(len(sl), sl[1])",
                '    print(" x ".strip().zfill(4), "Q".lower(), "a b".title())',
                '    print("AB".swapcase(), "Ab".capitalize(), "AB".casefold())',
                '    print("ab".startswith(("a", "z")), "ab".endswith(("x", "b")))',
                '    print("a-b".isalpha(), "12".isdigit(), "a1".isalnum())',
                '    print("  ".isspace(), "AB".isupper(), "ab".islower())',
                '    print("hi".ljust(4, "."), "hi".center(6), "hi".rjust(4))',
                '    print("file.txt".removesuffix(".txt"), "pfx_x".removeprefix("pfx_"))',
                '    print("a\\tb".expandtabs(4))',
                '    t: bytes = "go".encode("utf-8")',
                '    print(t.decode("utf-8"))',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r4_print_kwargs(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r4.py",
        "\n".join(
            [
                "def run() -> int:",
                '    print("a", "b", sep="-", end="!\\n")',
                '    print(1, 2, sep="")',
                '    print(b"hi\\n")',
                '    print("z", end="")',
                '    print("q")',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def test_r5_scalar_casts(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "r5.py",
        "\n".join(
            [
                "def run() -> int:",
                '    n: int = int("42")',
                '    m: int = int(" -7 ")',
                '    f: float = float("1.5")',
                "    print(n, m, f)",
                "    print(str(True), str(False), str(None))",
                '    print(str(b"ab"))',
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
