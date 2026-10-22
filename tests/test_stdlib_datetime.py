"""datetime: the script-shaped surface.

`datetime.strptime`, `combine`, `fromtimestamp`, the naive `timestamp`,
and the `date.min`/`date.max` class constants join the lowered set. Every
probe diffs stdout against the CPython oracle, and the strptime engine is
differentially fuzzed against CPython's `_strptime` in `test_strptime_fuzz`.
"""

from __future__ import annotations

import datetime as py_datetime
import random
import re
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func), got.stdout


def test_strptime_iso_shapes(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "strptime.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def run() -> int:",
                "    a: datetime.datetime = datetime.datetime.strptime('2020-01-02', '%Y-%m-%d')",
                "    print(a.year, a.month, a.day, a.hour)",
                "    b: datetime.datetime = datetime.datetime.strptime('2020-01-02T03:04:05', '%Y-%m-%dT%H:%M:%S')",
                "    print(b.isoformat())",
                "    c: datetime.datetime = datetime.datetime.strptime('2020-01-02 03:04:05.123456',"
                " '%Y-%m-%d %H:%M:%S.%f')",
                "    print(c.microsecond)",
                "    d: datetime.datetime = datetime.datetime.strptime('19991231235959', '%Y%m%d%H%M%S')",
                "    print(d.isoformat())",
                "    e: datetime.datetime = datetime.datetime.strptime('991231235959', '%y%m%d%H%M%S')",
                "    print(e.isoformat())",
                "    f: datetime.datetime = datetime.datetime.strptime('691231235959', '%y%m%d%H%M%S')",
                "    print(f.isoformat())",
                "    return 0",
                "",
            ]
        ),
    )


def test_strptime_names_and_ampm(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "names.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def run() -> int:",
                "    a: datetime.datetime = datetime.datetime.strptime('Feb 29, 2024', '%b %d, %Y')",
                "    print(a.isoformat())",
                "    b: datetime.datetime = datetime.datetime.strptime('January 05', '%B %d')",
                "    print(b.isoformat())",
                "    c: datetime.datetime = datetime.datetime.strptime('12:00:00 PM', '%I:%M:%S %p')",
                "    print(c.hour)",
                "    d: datetime.datetime = datetime.datetime.strptime('12:00:00 am', '%I:%M:%S %p')",
                "    print(d.hour)",
                "    e: datetime.datetime = datetime.datetime.strptime('Mon Jan 02', '%a %b %d')",
                "    print(e.isoformat())",
                "    return 0",
                "",
            ]
        ),
    )


def test_strptime_partial_digits_and_errors(tmp_path: Path) -> None:
    """The refusals print a flag, not the message: CPython words the messages
    differently per version, the accept and reject decisions do not change."""

    _oracle(
        tmp_path,
        "errors.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def run() -> int:",
                "    a: datetime.datetime = datetime.datetime.strptime('2020-1-2', '%Y-%m-%d')",
                "    print(a.isoformat())",
                "    b: datetime.datetime = datetime.datetime.strptime('5', '%d')",
                "    print(b.day)",
                "    try:",
                "        datetime.datetime.strptime('2020-01-02 tail', '%Y-%m-%d')",
                "        print('accepted')",
                "    except ValueError:",
                "        print('refused')",
                "    try:",
                "        datetime.datetime.strptime('2020', '%q')",
                "        print('accepted')",
                "    except ValueError:",
                "        print('refused')",
                "    try:",
                "        datetime.datetime.strptime('2020-02-30', '%Y-%m-%d')",
                "        print('accepted')",
                "    except ValueError:",
                "        print('refused')",
                "    try:",
                "        datetime.datetime.strptime('2020-01-32', '%Y-%m-%d')",
                "        print('accepted')",
                "    except ValueError:",
                "        print('refused')",
                "    return 0",
                "",
            ]
        ),
    )


def test_combine_and_fromtimestamp_and_timestamp(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "combine.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def run() -> int:",
                "    d: datetime.date = datetime.date(2020, 6, 1)",
                "    t: datetime.time = datetime.time(13, 45, 30, 250000)",
                "    combined: datetime.datetime = datetime.datetime.combine(d, t)",
                "    print(combined.isoformat())",
                "    ts: datetime.datetime = datetime.datetime.fromtimestamp(1591019130.25)",
                "    print(ts.year, ts.month, ts.day, ts.hour, ts.minute, ts.second, ts.microsecond)",
                "    back: float = datetime.datetime(2020, 1, 2, 3, 4, 5, 6).timestamp()",
                "    print('%.6f' % back)",
                "    day: datetime.date = datetime.date.fromtimestamp(1591019130.25)",
                "    print(day.isoformat())",
                "    return 0",
                "",
            ]
        ),
    )


def test_class_constants(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "consts.py",
        "\n".join(
            [
                "import datetime",
                "",
                "def run() -> int:",
                "    lo: datetime.date = datetime.date.min",
                "    hi: datetime.date = datetime.date.max",
                "    print(lo.year, lo.month, lo.day)",
                "    print(hi.year, hi.month, hi.day)",
                "    dtlo: datetime.datetime = datetime.datetime.min",
                "    dthi: datetime.datetime = datetime.datetime.max",
                "    print(dtlo.isoformat())",
                "    print(dthi.isoformat())",
                "    return 0",
                "",
            ]
        ),
    )


DIRECTIVES = ["%Y", "%y", "%m", "%d", "%H", "%I", "%M", "%S", "%f", "%j", "%w", "%p", "%b", "%B", "%a", "%A", "%%"]
LITERALS = ["-", "/", ":", "T", " ", ",", ""]
ALPHABET = "0123456789 -/:.,JanFebMarAprilMayJuneJulAugSepOctNovDecMonTueWedThuFriSatSunAMPmapm"


def _fuzz_case(rng: random.Random) -> tuple[str, str]:
    parts: list[str] = []
    for _ in range(rng.randint(1, 4)):
        if rng.random() < 0.4:
            parts.append(rng.choice(LITERALS))
        parts.append(rng.choice(DIRECTIVES))
    fmt = "".join(parts)
    text = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 20)))
    return fmt, text


def test_strptime_fuzz(tmp_path: Path) -> None:
    """One binary, many strptime calls: the compiled run must print the same
    lines CPython prints, where both either parse the pair or both refuse it."""

    rng = random.Random(20241021)
    cases = [_fuzz_case(rng) for _ in range(500)]

    lines: list[str] = ["import datetime", "", "def run() -> int:"]
    for fmt, text in cases:
        safe_text = text.replace("\\", "\\\\").replace("'", "\\'")
        safe_fmt = fmt.replace("'", "\\'")
        lines.append("    try:")
        lines.append(f"        value: datetime.datetime = datetime.datetime.strptime('{safe_text}', '{safe_fmt}')")
        lines.append("        print(value.isoformat())")
        lines.append("    except ValueError:")
        lines.append("        print('err')")
    lines.append("    return 0")
    lines.append("")

    source = "\n".join(lines)
    path = write_case(tmp_path, "fuzz.py", source)

    expected: list[str] = []
    for fmt, text in cases:
        try:
            value = py_datetime.datetime.strptime(text, fmt)
            expected.append(value.isoformat())
        except ValueError:
            expected.append("err")
        except re.error:
            expected.append("err")

    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    actual = got.stdout.splitlines()
    diffs = [(case, want, have) for case, want, have in zip(cases, expected, actual, strict=False) if want != have]
    assert not diffs, diffs[:10]
