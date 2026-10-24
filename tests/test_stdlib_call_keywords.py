"""Call-table keyword and arity rows over the builtin and stdlib surface.

Each row lowers keywords by reordering them into the positional call the
runtime helper takes; an unknown form fences with a named PS code. Probes
diff stdout, stderr and the exit code against the CPython 3.14 oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def _fence(tmp_path: Path, source: str, code: str, message: str) -> None:
    path = write_case(tmp_path, "fenced.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    combined = got.stderr + got.stdout
    assert code in combined, combined
    assert message in combined, combined


def test_int_base_keyword_and_positional_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "bases.py",
        "\n".join(
            [
                "def run() -> int:",
                "    a: int = int('ff', base=16)",
                "    b: int = int('777', 8)",
                "    c: int = int('-0xff', base=16)",
                "    d: int = int('102', 3)",
                "    e: int = int('Zz', base=36)",
                "    f: int = int(b'caFe', 16)",
                "    print(a, b, c, d, e, f)",
                "    return 0",
                "",
            ]
        ),
    )


def test_int_bad_base_raises_value_error(tmp_path: Path) -> None:
    # The ValueError message carries the base the caller gave, like CPython.
    for line in ("int('qq', 8)", "int('qq', base=8)"):
        path = write_case(tmp_path, "bad.py", f"def run() -> int:\n    print({line})\n    return 0\n")
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0, line
        assert "ValueError: invalid literal for int() with base 8" in got.stderr, line


def test_int_out_of_i64_range_stays_a_value_error(tmp_path: Path) -> None:
    # Base 16 over the i64 ceiling raise ValueError, the int-str subset, not
    # CPython's bignum.
    path = write_case(
        tmp_path,
        "big.py",
        "def run() -> int:\n    print(int('fffffffffffffffffffffffffffffffffffff', 16))\n    return 0\n",
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "ValueError" in got.stderr


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_int_unknown_forms_fence_with_named_codes() -> None:
    # base 0 and out-of-range constants fence with a named PS code; an
    # unknown keyword names itself.
    _fence("x: int = int('18', base=0)\n", "PS2003", "int base 0 is not lowered")
    _fence("x: int = int('18', base=99)\n", "PS2003", "int lowers base 2 to 36 only")
    _fence("x: int = int('ff', radix=16)\n", "PS2003", "unexpected keyword argument radix")
    _fence("x: int = int(255, base=16)\n", "PS2003", "takes a str or bytes value")
    _fence("x: int = int('ff', 16, base=10)\n", "PS2003", "base given more than once")


def test_open_encoding_read_write_match_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "enc.py",
        "\n".join(
            [
                "def run() -> int:",
                "    with open('data.txt', 'w', encoding='utf-8') as handle:",
                "        handle.write('alpha bravo\\ncharlie')",
                "    with open('data.txt', 'r', encoding='utf-8') as handle:",
                "        s: str = handle.read()",
                "    print(s)",
                "    with open('data.txt', encoding='ascii') as handle:",
                "        print(len(handle.read()) > 0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_open_unknown_forms_fence_with_named_codes() -> None:
    # Unknown keyword names itself; a non-listed encoding names the set.
    _fence("with open('a', errors='ignore') as h:\n    s: str = h.read()\n", "PS2003", "keyword errors is not lowered")
    _fence(
        "with open('a', 'r', encoding='utf-16') as h:\n    s: str = h.read()\n", "PS2001", "utf-8, ascii, or latin-1"
    )
    _fence("with open('a', 'r', -1) as h:\n    s: str = h.read()\n", "PS2003", "buffering is not lowered")
