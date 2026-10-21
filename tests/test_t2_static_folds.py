"""T2-5 static folds: builtin calls on compile-time constants fold at lowering.

`repr`/`str`/`hash`/`len`/`chr`/`ord`/`int`/`bool`/`abs` of a const argument
become const nodes; the oracle tests diff compiled stdout against CPython,
including repr edge characters. `eval`/`exec` stay fenced — except the
degenerate `eval(<string-const>)` where `ast.literal_eval` proves the source
safe and the result is a scalar (the tier-2 boundary: no arbitrary eval).
`hash` folds only on int/bool; str/bytes hashing is seed-randomized and stays
tier-3. `int` of a text const keeps the ps_int_from_bytes path (the subset's
i64 fence and underscore rejection would be bypassed by folding). Each fence
test asserts the compile still fails with a fence.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def _fences(source: str) -> list[dict[str, object]]:
    return lower_source(source)["fences"]


def test_scalar_folds_match_cpython(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_scalars.py",
        """
def probe() -> int:
    return (
        len("hello")
        + ord("A")
        + ord(chr(66))
        + abs(-3)
        + abs(2 - 5)
        + int("41")
        + int(2.9)
        + int(True)
        + hash(7)
        + hash(-9)
        + hash(True)
        + len([1, 2, 3])
        + len({"a": 1, "b": 2})
    )
""",
    )


def test_repr_folds_edge_chars_match_cpython(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_repr.py",
        """
def probe() -> str:
    parts: str = (
        repr("it's")
        + "|"
        + repr('say "hi"')
        + "|"
        + repr("a\\nb")
        + "|"
        + repr("tab\\tnewline\\n")
        + "|"
        + repr("café")
        + "|"
        + repr("trail\\\\")
    )
    return parts + "|" + repr(b"\\x00\\xffA") + "|" + repr(42) + "|" + repr(1.5) + "|" + repr(True)
""",
    )


def test_str_and_const_names_match_cpython(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_str_names.py",
        """
LIMIT: int = 40
WORD: str = "xy"


def probe() -> str:
    return (
        str(42)
        + str(LIMIT + 2)
        + str(True)
        + repr(WORD)
        + str(len(WORD))
        + str(bool(""))
        + str(bool("x"))
    )
""",
    )


def test_eval_literal_scalar_folds(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "t2_eval.py",
        """
def probe() -> int:
    return eval("40") + eval("-2") + eval("0x10")
""",
    )


def test_folded_ir_has_no_builtin_call() -> None:
    mod = lower_source(
        """
def probe() -> int:
    return len("abc") + ord("z") + abs(-2) + int(9.7) + hash(5)
"""
    )
    assert mod["fences"] == []
    body = mod["functions"][0]["body"]

    found: list[dict[str, object]] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("kind") == "call":
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)
    assert found == []


def test_eval_non_literal_stays_fenced() -> None:
    fences = _fences(
        """
def probe() -> int:
    return eval("x + 1")
"""
    )
    assert len(fences) == 1
    assert fences[0]["code"] == "PS2002"
    assert fences[0]["message"] == "call to eval is not lowered"


def test_eval_non_scalar_stays_fenced() -> None:
    fences = _fences(
        """
def probe() -> int:
    return len(eval("[1, 2]"))
"""
    )
    assert len(fences) == 1
    assert fences[0]["message"] == "call to eval is not lowered"


def test_exec_stays_fenced() -> None:
    fences = _fences(
        """
def probe() -> int:
    exec("x = 1")
    return 0
"""
    )
    assert len(fences) == 1
    assert fences[0]["message"] == "call to exec is not lowered"


def test_hash_str_stays_fenced() -> None:
    fences = _fences(
        """
def probe() -> int:
    return hash("seed")
"""
    )
    assert len(fences) == 1
    assert fences[0]["message"] == "call to hash is not lowered"


def test_chr_outside_latin1_keeps_runtime_path() -> None:
    # chr(256) is valid CPython but outside the str-const Latin-1 boundary,
    # so the fold declines and the call lowers to ps_chr like before.
    fences = _fences(
        """
def probe() -> str:
    return chr(256)
"""
    )
    assert fences == []
