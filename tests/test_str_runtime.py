"""The S2 runtime differential: every str method on non-ASCII operands.

Each case is a self-contained program; CPython 3.14 runs it first and the
compiled binary must match byte for byte on stdout (the S2 receipt, the
probe set S7 calibrates). One test per method family, per the S2 plan.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

CAFE = "caf\u00e9"
EUR = "\u20ac"
SIGMA = "\u03a3"
SZ = "\u00df"


def _diff(tmp_path: Path, name: str, lines: list[str]) -> None:
    source = "\n".join(lines) + "\n"
    path = write_case(tmp_path, name, source)
    expected = subprocess.run(  # noqa: S603
        [sys.executable, str(path)], capture_output=True, check=True
    ).stdout
    binary = tmp_path / (name + ".bin")
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    binary.chmod(0o755)
    actual = subprocess.run(  # noqa: S603
        [str(binary)], capture_output=True, timeout=30
    )
    assert actual.stdout == expected, f"{name}: stdout differs\n cpython: {expected!r}\n compiled: {actual.stdout!r}"
    assert actual.returncode == 0, actual.stderr


def test_len_counts_code_points(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "len_cp.py",
        [
            f"print(len('{CAFE}'), len('{EUR}'), len(''), len('ab'), len('{SZ}{SZ}'))",
        ],
    )


def test_index_gets_code_points(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "index_cp.py",
        [
            f"s: str = '{CAFE}'",
            "print(s[0], s[3], s[-1])",
        ],
    )


def test_slice_on_code_points(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "slice_cp.py",
        [
            f"s: str = '{CAFE}'",
            "print(s[1:3], s[:2], s[2:], s[:])",
        ],
    )


def test_find_and_rfind_offsets(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "find_cp.py",
        [
            f"s: str = 'ab{CAFE}db{CAFE}'",
            "print(s.find('f'), s.find('b'), s.rfind('b'), s.find('x'), s.rfind('x'))",
            f"print('x{EUR}yx{EUR}'.find('{EUR}'), 'x{EUR}yx{EUR}'.rfind('{EUR}'))",
        ],
    )


def test_count_and_index_offsets(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "count_cp.py",
        [
            f"s: str = 'a{EUR}a{EUR}a'",
            "print(s.count('a'), s.count('a'), s.count(''))",
        ],
    )


def test_upper_lower_full_mappings(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "case_basic.py",
        [
            f"print('{CAFE}'.upper())",
            f"print('{SZ}'.upper())",
            f"print('{CAFE}'.lower())",
            f"print('{SIGMA}'.lower())",
        ],
    )


def test_final_sigma_rule(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "case_sigma.py",
        [
            "print('\u039f\u03a3'.lower())",
            "print('\u039f\u03a3\u039f'.lower())",
            "print('\u03a3'.lower())",
            "print('a\u03a3'.lower())",
            "print('a\u03a3b'.lower())",
            "print('\u039f\u03a3 '.lower())",
        ],
    )


def test_title_word_scans(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "case_title.py",
        [
            f"print('{CAFE} byte'.title())",
            'print("don\'t stop".title())',
            "print('x1y2'.title())",
            "print('\u03a3\u039f\u03a6\u039f\u03a3'.title())",
        ],
    )


def test_capitalize_and_casefold(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "case_caps.py",
        [
            f"print('{CAFE} BYTE'.capitalize())",
            f"print('{SZ}'.casefold())",
            f"print('{SIGMA}'.casefold())",
        ],
    )


def test_swapcase_context(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "case_swap.py",
        [
            "print('Hello, World'.swapcase())",
            "print('\u039f\u03a3\u039f'.swapcase())",
            f"print('{CAFE}'.swapcase())",
        ],
    )


def test_startswith_endswith(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "ends_cp.py",
        [
            f"s: str = '{CAFE}.'",
            "print(s.startswith('caf'), s.startswith('caf'), s.endswith('.'), s.endswith('!'))",
            f"print('{EUR}5'.startswith('{EUR}'))",
        ],
    )


def test_strip_family_boundaries(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "strip_cp.py",
        [
            f"print('  {CAFE}  '.strip())",
            f"print('  {CAFE}  '.lstrip())",
            f"print('  {CAFE}  '.rstrip())",
        ],
    )


def test_replace_on_utf8(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "replace_cp.py",
        [
            f"print('{CAFE}'.replace('f', 'ph'))",
            f"print('a{EUR}b{EUR}'.replace('{EUR}', 'E'))",
            f"print('aaa'.replace('a', '{CAFE}'))",
        ],
    )


def test_split_and_join(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "split_cp.py",
        [
            f"parts: list[str] = '{CAFE},b,{EUR}'.split(',')",
            "print(len(parts), parts[0], parts[1], parts[2])",
            f"print(','.join(['x', 'y', 'z']))",
            f"print('{CAFE}'.join(['1', '2']))",
        ],
    )


def test_partition_variants(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "part_cp.py",
        [
            f"a: str = 'k{CAFE}y=v'",
            "head: str = a.partition('=')[0]",
            "sep: str = a.partition('=')[1]",
            "tail: str = a.partition('=')[2]",
            "print(head, sep, tail)",
            f"b: str = 'k{CAFE}y=v=w'",
            "print(b.rpartition('=')[0], b.rpartition('=')[1], b.rpartition('=')[2])",
        ],
    )


def test_zfill_and_justify_widths(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "justify_cp.py",
        [
            f"print('{CAFE}'.ljust(8, '.'))",
            f"print('{CAFE}'.rjust(8, '.'))",
            f"print('-{CAFE}'.zfill(8))",
            f"print('{CAFE}'.center(9, '*'))",
        ],
    )


def test_expandtabs_columns(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "tabs_cp.py",
        [
            f"print('a{CAFE}\tb'.expandtabs(4))",
            "print('ab\tcd'.expandtabs())",
        ],
    )


def test_removeprefix_removesuffix(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "prefix_cp.py",
        [
            f"print('{CAFE}x'.removeprefix('{CAFE}'))",
            f"print('x{CAFE}'.removesuffix('{CAFE}'))",
        ],
    )


def test_encode_decode_roundtrip(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "codec_cp.py",
        [
            f"s: str = '{CAFE}'",
            "print(s.encode('utf-8'))",
            "print(s.encode('utf-8').decode('utf-8'))",
            "print(len(s.encode('utf-8')))",
        ],
    )


def test_ord_chr_full_range(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "ordchr_cp.py",
        [
            f"print(ord('{CAFE}'[3]), ord('{EUR}'))",
            f"print(chr(0x20AC), chr(233), chr(0x1F600))",
            "print(chr(0x10FFFF) != '')",
        ],
    )


def test_comparison_ordering(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "cmp_cp.py",
        [
            f"print('{CAFE}' < 'cafz', '{CAFE}' > 'caf', '{CAFE}' == '{CAFE}')",
        ],
    )


def test_equality_and_membership(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "member_cp.py",
        [
            f"print('f' in '{CAFE}', 'g' in '{CAFE}')",
            f"print('{CAFE}' in 'x{CAFE}y')",
        ],
    )
