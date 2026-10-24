"""The S3 runtime differential: format, the is* predicates and repr.

Each case is a self-contained program; CPython 3.14 runs it first and the
compiled binary must match byte for byte on stdout. These are the S3
receipts from the code-point str model plan: format widths and precisions
count code points, the shared repr engine follows CPython's escape rules,
and the is* predicates agree on everything the case tables decide (the
S4-only categories raise the named fence instead of guessing).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

CAFE = "caf\u00e9"
EUR = "\u20ac"
EMOJI = "\U0001f642"
COMB = "a\u0301bc"
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


def test_repr_escape_rules(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "repr_cp.py",
        [
            f"print(repr('{CAFE}'), repr('{EUR}'), repr('{EMOJI}'), repr('{SZ}'))",
            "print(repr('a	b'), repr('a\\\\b'), repr(\"a'b\"), repr('a\"b'))",
            "print(repr(''))",
            f"print(repr('{COMB}'))",
        ],
    )


def test_repr_through_format_and_percent(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "repr_paths.py",
        [
            f"print('%r' % '{CAFE}', f\"{{'{EUR}'!r}}\")",
            "print('%c' % 0x20ac, '%c' % 0x1f642)",
            'print(f"{chr(0x3a3)}", f"{0x3a3:c}")',
        ],
    )


def test_format_widths_count_code_points(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "fmt_cp.py",
        [
            f"print(f\"{{'{CAFE}':>8}}|\", f\"{{'{EUR}':<6}}|\", f\"{{'{EMOJI}':^7}}|\")",
            f"print(f\"{{'{COMB}':.2}}|\", f\"{{'{EMOJI}{EUR}':.1}}|\")",
            f"print(f\"{{'{CAFE}':>08.3}}|\", f\"{{'{'ab'}':*^9}}|\")",
            "print('{}'.format(1), '{:>4}'.format('x'))",
        ],
    )


def test_format_ascii_widths_unchanged(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "fmt_ascii.py",
        [
            'print(f"{\'ab\':>6}|", f"{\'ab\':06.3}|", f"{3.14159:.2f}")',
            "print('%5s|%-5s|' % ('a', 'b'))",
        ],
    )


def test_predicates_on_case_table_answers(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "pred_cp.py",
        [
            f"print('{SZ}'.islower(), '{SZ}'.upper().isupper(), 'IX'.islower())",
            f"print('abc'.isalpha(), '{CAFE}'.isalpha(), 'ab1'.isalpha())",
            "print('123'.isdigit(), ''.isalpha(), ' '.isspace(), '\\t'.isspace())",
            "print('Ab'.isupper(), 'aB'.islower(), ''.isspace(), ''.isdigit())",
        ],
    )


def test_isascii_decides_everything(tmp_path: Path) -> None:
    _diff(
        tmp_path,
        "isascii_cp.py",
        [
            f"print('abc'.isascii(), ''.isascii(), '{CAFE}'.isascii(), '{EUR}'.isascii())",
            f"print('{EMOJI}'.isascii(), 'ab\\t'.isascii())",
        ],
    )


def test_predicate_fence_on_s4_categories(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "pred_fence.py",
        "print('\U0001f642'.isalpha())\n",
    )
    binary = tmp_path / "pred_fence.bin"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    binary.chmod(0o755)
    actual = subprocess.run(  # noqa: S603
        [str(binary)], capture_output=True, timeout=30
    )
    assert actual.returncode != 0
    text = actual.stderr.decode("utf-8", "replace")
    assert "NotImplementedError" in text, text
    assert "str.isalpha" in text, text


def test_index_error_message(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "index_msg.py",
        "print('abc'.index('z'))\n",
    )
    binary = tmp_path / "index_msg.bin"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    binary.chmod(0o755)
    actual = subprocess.run(  # noqa: S603
        [str(binary)], capture_output=True, timeout=30
    )
    assert actual.returncode != 0
    text = actual.stderr.decode("utf-8", "replace")
    assert "substring not found" in text, text
