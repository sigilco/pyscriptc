"""E1 item C20: `sorted(seq, key=lambda w: expr)` is a narrow decorate slice.

This is not the callables epic. The lambda must be one positional parameter
and a plain scalar-or-tuple body; the lowering builds one key list per key
field in a synthesized extraction loop, stable-sorts an index list by those
keys, and rebuilds the element list through the permutation. Every other
`key=` shape keeps a precise fence, as does a non-literal `reverse=`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case

from pyscriptc.lower import lower_source


def _entry(mod: dict) -> dict | None:
    return next((fn for fn in mod["functions"] if fn["name"] == mod["entry"]), None)


def _names(stmts: list[dict]) -> set[str]:
    found: set[str] = set()

    def walk(body: list[dict]) -> None:
        for stmt in body:
            if stmt.get("target"):
                found.add(stmt["target"])
            for key in ("body", "then", "else"):
                if isinstance(stmt.get(key), list):
                    walk(stmt[key])

    walk(stmts)
    return found


def test_sorted_key_lambda_sorts_by_field() -> None:
    mod = lower_source(
        """
def probe(xs: list[str]) -> list[str]:
    return sorted(xs, key=lambda w: w)
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    names = _names(entry["body"])
    # One key list for the one field, plus the element and index lists.
    assert any("k0" in name for name in names)
    assert any("idx" in name for name in names)


def test_sorted_key_over_dict_iterates_keys() -> None:
    mod = lower_source(
        """
def probe(d: dict[str, int]) -> list[str]:
    return sorted(d, key=lambda w: (-d[w], w))
"""
    )
    assert mod["fences"] == []
    entry = _entry(mod)
    assert entry is not None
    names = _names(entry["body"])
    assert any("k0" in name for name in names)
    assert any("k1" in name for name in names)


def test_sorted_key_rejects_non_lambda() -> None:
    mod = lower_source(
        """
def f(w: str) -> str:
    return w

def probe(xs: list[str]) -> list[str]:
    return sorted(xs, key=f)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2003"
    assert "sorted key= takes an inline lambda" in mod["fences"][0]["message"]


def test_sorted_key_rejects_multi_param_lambda() -> None:
    mod = lower_source(
        """
def probe(xs: list[str]) -> list[str]:
    return sorted(xs, key=lambda a, b: a)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2003"
    assert "one positional parameter" in mod["fences"][0]["message"]


def test_sorted_key_rejects_lambda_inside_key() -> None:
    mod = lower_source(
        """
def probe(xs: list[str]) -> list[str]:
    return sorted(xs, key=lambda w: (lambda q: q)(w))
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2003"
    assert "a sorted key= lambda is a plain expression" in mod["fences"][0]["message"]


def test_sorted_reverse_keyword_lowers() -> None:
    mod = lower_source(
        """
def probe(xs: list[int]) -> list[int]:
    return sorted(xs, reverse=True)
"""
    )
    assert mod["fences"] == []


def test_sorted_reverse_nonliteral_still_fences() -> None:
    mod = lower_source(
        """
def probe(xs: list[int], flip: bool) -> list[int]:
    return sorted(xs, reverse=flip)
"""
    )
    assert len(mod["fences"]) == 1
    assert mod["fences"][0]["code"] == "PS2003"


def test_dict_get_two_args_lowers() -> None:
    mod = lower_source(
        """
def probe(d: dict[str, int], w: str) -> int:
    return d.get(w, 0)
"""
    )
    assert mod["fences"] == []


def test_dict_get_one_arg_lowers_optional() -> None:
    # R26: the 1-arg form now has an IR type — `val | None` as a tagged cell.
    mod = lower_source(
        """
def probe(d: dict[str, int], w: str) -> int | None:
    return d.get(w)
"""
    )
    assert mod["fences"] == []
    probe = next(fn for fn in mod["functions"] if fn["name"] == "probe")
    assert probe["returnType"]["optional"] is True
    assert probe["returnType"]["elem"] == {"kind": "int"}


def test_sorted_key_values_match_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sortedkey_vals.py",
        "\n".join(
            [
                "def probe() -> str:",
                '    words: list[str] = ["fig", "apple", "kiwi", "banana", "plum"]',
                "    a: list[str] = sorted(words, key=lambda w: w)",
                "    b: list[str] = sorted(words, key=lambda w: len(w))",
                "    c: list[str] = sorted(words, key=lambda w: (-len(w), w))",
                '    return a[0] + a[1] + a[4] + "|" + b[0] + b[1] + "|" + c[0] + c[1]',
                "",
            ]
        ),
    )
    out = str(tmp_path / "sortedkey_vals")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


def test_sorted_key_is_stable(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sortedkey_stable.py",
        "\n".join(
            [
                "def probe() -> int:",
                '    words: list[str] = ["bb", "aa", "c", "dd", "a", "b"]',
                "    got: list[str] = sorted(words, key=lambda w: len(w))",
                '    want: list[str] = ["c", "a", "b", "bb", "aa", "dd"]',
                "    i: int = 0",
                "    same: int = 1",
                "    while i < len(got):",
                "        if got[i] != want[i]:",
                "            same = 0",
                "        i = i + 1",
                "    return same",
                "",
            ]
        ),
    )
    out = str(tmp_path / "sortedkey_stable")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "1"


def test_sorted_key_over_dict_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sortedkey_dict.py",
        "\n".join(
            [
                "def probe() -> int:",
                "    counts: dict[str, int] = {}",
                '    for w in "b a c a b a".split():',
                "        counts[w] = counts.get(w, 0) + 1",
                "    keys: list[str] = sorted(counts, key=lambda w: (-counts[w], w))",
                "    total: int = 0",
                "    i: int = 0",
                "    while i < len(keys):",
                "        total = total * 10 + counts[keys[i]]",
                "        i = i + 1",
                "    return total",
                "",
            ]
        ),
    )
    out = str(tmp_path / "sortedkey_dict")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout.strip() == "321"


def test_word_freq_output_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "word_freq_like.py",
        "\n".join(
            [
                'text = "the quick brown fox jumps over the lazy dog'
                ' the fox barks and the dog runs away from the quick fox"',
                "counts: dict[str, int] = {}",
                "for word in text.split():",
                "    counts[word] = counts.get(word, 0) + 1",
                "for word in sorted(counts, key=lambda w: (-counts[w], w)):",
                "    print(word, counts[word])",
                "",
            ]
        ),
    )
    out = str(tmp_path / "word_freq_like")
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", out)
    assert got.returncode == 0, got.stderr
    # The program is a script, so the oracle is the interpreter on the file.
    want = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True)
    assert got.stdout == want.stdout


if __name__ == "__main__":
    sys.exit(0)
