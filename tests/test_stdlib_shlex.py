"""shlex: the closed subset.

`split`, `quote` and `join` lower natively. The deterministic rows diff
against the CPython oracle word for word, including the error cases: an
unterminated quote and a trailing backslash raise ValueError, which is what
CPython's `shlex` raises.
"""

from __future__ import annotations

import shlex as py_shlex
from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def _split_lines(words: list[str]) -> str:
    """The oracle's word list, one word per line, with the count first."""
    return "\n".join([str(len(words)), *words])


def test_split_matches_cpython(tmp_path: Path) -> None:
    cases = [
        "a b c",
        "  a   b  ",
        "one 'two three' four",
        'one "two three" four',
        "a\\ b c",
        "",
        "   ",
        "a#b",
        "a #b",
        "'quoted'",
        '""',
        "''",
    ]
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            *[
                "\n".join(
                    [
                        f"    words = shlex.split({text!r})",
                        "    print(len(words))",
                        "    for word in words:",
                        "        print(word)",
                    ]
                )
                for text in cases
            ],
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(_split_lines(py_shlex.split(text)) for text in cases)
    assert _run(tmp_path, "split.py", source) == expected + "\n0\n"


def test_split_comments_matches_cpython(tmp_path: Path) -> None:
    cases = [
        "a b # comment",
        "a #b",
        "a#b",
        "# only a comment",
        "one 'two # three' four",
        'one "two # three" four',
    ]
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            *[
                "\n".join(
                    [
                        f"    words = shlex.split({text!r}, comments=True)",
                        "    print(len(words))",
                        "    for word in words:",
                        "        print(word)",
                    ]
                )
                for text in cases
            ],
            "    return 0",
            "",
        ]
    )
    expected = "\n".join(_split_lines(py_shlex.split(text, comments=True)) for text in cases)
    assert _run(tmp_path, "comments.py", source) == expected + "\n0\n"


def test_split_without_comments_keeps_the_hash(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    words = shlex.split('a #b')",
            "    print(len(words))",
            "    for word in words:",
            "        print(word)",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "hash.py", source) == _split_lines(py_shlex.split("a #b")) + "\n0\n"


def test_quote_matches_cpython(tmp_path: Path) -> None:
    cases = [
        "",
        "abc",
        "a b",
        "a'b",
        "a'b'c",
        "@%+=:,./-_",
        "with space",
        "tab\there",
        "newline\nhere",
    ]
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            *[f"    print(shlex.quote({text!r}))" for text in cases],
            "    return 0",
            "",
        ]
    )
    expected = "\n".join([py_shlex.quote(text) for text in cases])
    assert _run(tmp_path, "quote.py", source) == expected + "\n0\n"


def test_quote_quotes_a_non_ascii_word(tmp_path: Path) -> None:
    # The harness reads the output as latin-1, so the non-ASCII word is
    # compared byte for byte through its escapes.
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    quoted = shlex.quote('\\u00fcn\\u00efcode')",
            "    print(len(quoted))",
            "    print(quoted == \"'\\u00fcn\\u00efcode'\")",
            "    return 0",
            "",
        ]
    )
    expected = f"{len(py_shlex.quote('\u00fcn\u00efcode'))}\nTrue\n0\n"
    assert _run(tmp_path, "unicode.py", source) == expected


def test_join_matches_cpython(tmp_path: Path) -> None:
    cases = [
        ["a"],
        ["a", "b"],
        ["a b", "c"],
        ["a'b", "c"],
        [""],
    ]
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    words: list[str] = []",
            "    print(shlex.join(words))",
            *[f"    print(shlex.join({words!r}))" for words in cases],
            "    return 0",
            "",
        ]
    )
    expected = "\n".join([py_shlex.join([]), *[py_shlex.join(words) for words in cases]])
    assert _run(tmp_path, "join.py", source) == expected + "\n0\n"


def test_split_round_trips_through_join(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    print(shlex.join(['a b', \"c'd\", 'e']))",
            "    words = shlex.split(shlex.join(['a b', \"c'd\", 'e']))",
            "    print(len(words))",
            "    for word in words:",
            "        print(word)",
            "    return 0",
            "",
        ]
    )
    words = ["a b", "c'd", "e"]
    expected = "\n".join([py_shlex.join(words), _split_lines(words)])
    assert _run(tmp_path, "roundtrip.py", source) == expected + "\n0\n"


def test_unterminated_quote_is_valueerror(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    try:",
            '        shlex.split("unterminated \'quote")',
            "    except ValueError:",
            "        print('unterminated')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "unterminated.py", source) == "unterminated\n0\n"


def test_trailing_escape_is_valueerror(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import shlex",
            "",
            "def run() -> int:",
            "    try:",
            "        shlex.split('trailing\\\\')",
            "    except ValueError:",
            "        print('escaped')",
            "    return 0",
            "",
        ]
    )
    assert _run(tmp_path, "escape.py", source) == "escaped\n0\n"


def test_from_import_binds(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "from shlex import split, quote",
            "",
            "def run() -> int:",
            "    words = split('a b')",
            "    print(len(words))",
            "    for word in words:",
            "        print(word)",
            "    print(quote('a b'))",
            "    return 0",
            "",
        ]
    )
    expected = "\n".join([_split_lines(py_shlex.split("a b")), py_shlex.quote("a b")])
    assert _run(tmp_path, "from_import.py", source) == expected + "\n0\n"
