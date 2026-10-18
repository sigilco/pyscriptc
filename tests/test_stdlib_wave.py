"""The codec wave lowers a closed subset and still fences names outside that surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import EXAMPLES, compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_wave_probe_matches_cpython(tmp_path: Path) -> None:
    path = EXAMPLES / "wave_probe.py"
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    assert got.stdout == "0\n"


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_from_import_of_a_lowered_function(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "sha.py",
        "\n".join(
            [
                "from hashlib import sha256",
                "def f() -> str:",
                "    return sha256(b'abc').hexdigest()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "f")


def test_codec_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        (
            "finditer.py",
            "import re\n\ndef f(s: str) -> int:\n    re.finditer('a', s)\n    return 1\n",
            "PS2001",
            "re.finditer",
        ),
        (
            "load.py",
            "import json\n\ndef f(s: str) -> int:\n    json.load(s)  # type: ignore[arg-type]\n    return 1\n",
            "PS2001",
            "json.load",
        ),
        (
            "request.py",
            "\n".join(
                [
                    "import urllib",
                    "def f() -> int:",
                    "    urllib.request.urlopen('http://example')  # type: ignore[attr-defined]",
                    "    return 1",
                    "",
                ]
            ),
            "PS2001",
            "urllib.request.urlopen",
        ),
        (
            "encodebytes.py",
            "import base64\n\ndef f(b: bytes) -> bytes:\n    return base64.encodebytes(b)\n",
            "PS2001",
            "base64.encodebytes",
        ),
        (
            "sha3.py",
            "import hashlib\n\ndef f(b: bytes) -> str:\n    return hashlib.sha3_256(b).hexdigest()\n",
            "PS2001",
            "hashlib.sha3_256",
        ),
        (
            "calcsize.py",
            "import struct\n\ndef f() -> int:\n    return struct.calcsize('<H')\n",
            "PS2001",
            "struct.calcsize",
        ),
        (
            "groups.py",
            "import re\n\ndef f() -> str:\n    return re.findall(r'(a)(b)(c)(d)(e)', 'abcde')[0]\n",
            "PS2002",
            "four",
        ),
        (
            "plain.py",
            "import json\n\ndef f(s: str) -> int:\n    json.loads(s)\n    return 1\n",
            "PS1001",
            "json.loads needs the result annotation",
        ),
        (
            "intkeys.py",
            "import json\n\ndef f() -> str:\n    return json.dumps({1: 'a'})\n",
            "PS2001",
            "JSON object keys are str",
        ),
        (
            "dynfmt.py",
            "import struct\n\ndef f(fmt: str, data: bytes) -> int:\n    return struct.unpack(fmt, data)[0]\n",
            "PS2001",
            "string literal",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_random_matches_cpython(tmp_path: Path) -> None:
    cases = [
        (
            "randint.py",
            "import random\n\ndef f() -> int:\n    random.seed(1)\n    return random.randint(0, 1)\n",
            "0\n",
        ),
        (
            "randfloat.py",
            "import random\n\ndef f() -> float:\n    random.seed(1)\n    return random.random()\n",
            "0.13436424411240122\n",
        ),
        (
            "randbits.py",
            "import random\n\ndef f() -> int:\n    random.seed(42)\n    return random.getrandbits(32)\n",
            None,
        ),
        (
            "randrange.py",
            "\n".join(
                [
                    "import random",
                    "def f() -> int:",
                    "    random.seed(7)",
                    "    return random.randrange(2, 10, 3) * 100 + random.randrange(5)",
                    "",
                ]
            ),
            None,
        ),
        (
            "randuniform.py",
            "import random\n\ndef f() -> float:\n    random.seed(3)\n    return random.uniform(1.0, 2.0)\n",
            None,
        ),
        (
            "randchoice.py",
            "\n".join(
                [
                    "import random",
                    "def f() -> int:",
                    "    random.seed(9)",
                    "    xs: list[int] = [10, 20, 30, 40]",
                    "    return random.choice(xs) + random.choice([1, 2])",
                    "",
                ]
            ),
            None,
        ),
        (
            "randshuffle.py",
            "\n".join(
                [
                    "import random",
                    "def f() -> int:",
                    "    random.seed(5)",
                    "    xs: list[int] = [1, 2, 3, 4, 5]",
                    "    random.shuffle(xs)",
                    "    return xs[0] * 1000 + xs[4]",
                    "",
                ]
            ),
            None,
        ),
    ]
    for name, source, want in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == oracle(path, "f"), name
        if want is not None:
            assert got.stdout == want


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_csv_matches_cpython(tmp_path: Path) -> None:
    cases = [
        (
            "readlines.py",
            "\n".join(
                [
                    "import csv",
                    "def f() -> int:",
                    "    total: int = 0",
                    '    for row in csv.reader(["a,\\"x\\ny\\"", "z,2", ""]):',
                    "        total += len(row)",
                    "    return total",
                    "",
                ]
            ),
        ),
        (
            "readio.py",
            "\n".join(
                [
                    "import csv",
                    "import io",
                    "def f() -> int:",
                    "    buf: io.StringIO = io.StringIO('a,\"x\\ny\"\\r\\nz,2\\r\\n')",
                    "    total: int = 0",
                    "    for row in csv.reader(buf):",
                    "        for cell in row:",
                    "            total += len(cell)",
                    "    return total",
                    "",
                ]
            ),
        ),
        (
            "readfile.py",
            "\n".join(
                [
                    "import csv",
                    "from typing import TextIO",
                    "def f(path: str) -> int:",
                    "    fh: TextIO = open(path)",
                    "    total: int = 0",
                    "    for row in csv.reader(fh):",
                    "        for cell in row:",
                    "            total += len(cell)",
                    "    return total",
                    "",
                ]
            ),
        ),
        (
            "writebuf.py",
            "\n".join(
                [
                    "import csv",
                    "import io",
                    "def f() -> int:",
                    "    buf: io.StringIO = io.StringIO()",
                    "    wr: csv._writer = csv.writer(buf)  # type: ignore[name-defined]",
                    '    wr.writerow(["a", "b,c", \'d"e\', ""])',
                    '    wr.writerows([["x"], ["y\\nz"]])',
                    "    return len(buf.getvalue())",
                    "",
                ]
            ),
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        args = f'["{tmp_path / "data.csv"}"]' if name == "readfile.py" else "[]"
        if name == "readfile.py":
            (tmp_path / "data.csv").write_text('a,"x\ny"\r\nz,2\r\n', encoding="utf-8")
        got = compile_run(tmp_path, path, "f", args)
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == oracle(path, "f", args), name


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_textwrap_matches_cpython(tmp_path: Path) -> None:
    cases = [
        (
            "fill.py",
            "\n".join(
                [
                    "import textwrap",
                    "def f() -> str:",
                    "    return textwrap.fill(",
                    '        "the quick brown fox jumps over the lazy dog", 15',
                    "    )",
                    "",
                ]
            ),
        ),
        (
            "wrap.py",
            "\n".join(
                [
                    "import textwrap",
                    "def f() -> int:",
                    "    return len(",
                    '        textwrap.wrap("one two-three four---five  six", 9)',
                    "    )",
                    "",
                ]
            ),
        ),
        (
            "shorten.py",
            "\n".join(
                [
                    "import textwrap",
                    "def f() -> str:",
                    '    return textwrap.shorten("hello there world", 12)',
                    "",
                ]
            ),
        ),
        (
            "dedent.py",
            "\n".join(
                [
                    "import textwrap",
                    "def f() -> str:",
                    '    return textwrap.dedent("    a\\n      b\\n    c\\n")',
                    "",
                ]
            ),
        ),
        (
            "indent.py",
            "\n".join(
                [
                    "import textwrap",
                    "def f() -> str:",
                    '    return textwrap.indent("a\\n\\nb\\n", ">")',
                    "",
                ]
            ),
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == oracle(path, "f"), name


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_copy_matches_cpython(tmp_path: Path) -> None:
    cases = [
        (
            "copylist.py",
            "\n".join(
                [
                    "import copy",
                    "def f() -> int:",
                    "    xs: list[int] = [1, 2, 3]",
                    "    ys: list[int] = copy.copy(xs)",
                    "    zs: list[int] = copy.deepcopy(xs)",
                    "    ys.append(4)",
                    "    return len(xs) + len(ys) + len(zs)",
                    "",
                ]
            ),
        ),
        (
            "deepnested.py",
            "\n".join(
                [
                    "import copy",
                    "def f() -> int:",
                    "    inner: list[int] = [1, 2]",
                    "    xs: list[list[int]] = [inner, [3]]",
                    "    ys: list[list[int]] = copy.deepcopy(xs)",
                    "    inner.append(9)",
                    "    return len(xs[0]) + len(ys[0])",
                    "",
                ]
            ),
        ),
        (
            "deepdict.py",
            "\n".join(
                [
                    "import copy",
                    "def f() -> int:",
                    "    inner: list[int] = [1]",
                    "    xs: dict[str, list[int]] = {'a': inner, 'b': [2, 3]}",
                    "    ys: dict[str, list[int]] = copy.deepcopy(xs)",
                    "    inner.append(9)",
                    "    return len(ys['a']) + len(ys['b']) + len(xs['a'])",
                    "",
                ]
            ),
        ),
        (
            "copydict.py",
            "\n".join(
                [
                    "import copy",
                    "def f() -> int:",
                    "    xs: dict[str, int] = {'a': 1}",
                    "    ys: dict[str, int] = copy.copy(xs)",
                    "    xs['b'] = 2",
                    "    return len(ys) + len(xs)",
                    "",
                ]
            ),
        ),
    ]
    for name, source in cases:
        path = write_case(tmp_path, name, source)
        got = compile_run(tmp_path, path, "f")
        assert got.returncode == 0, f"{name}\n{got.stderr}"
        assert got.stdout == oracle(path, "f"), name


def test_wave_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        (
            "gauss.py",
            "import random\n\ndef f() -> float:\n    return random.gauss(0, 1)\n",
            "PS2001",
            "random.gauss",
        ),
        (
            "seedstr.py",
            "import random\n\ndef f() -> int:\n    random.seed('x')  # type: ignore[arg-type]\n    return 1\n",
            "PS1003",
            "random.seed argument is int",
        ),
        (
            "dictreader.py",
            "import csv\n\ndef f() -> int:\n    csv.DictReader(['a'])\n    return 1\n",
            "PS2001",
            "csv.DictReader",
        ),
        (
            "wrapper.py",
            "import textwrap\n\ndef f() -> int:\n    textwrap.TextWrapper()\n    return 1\n",
            "PS2001",
            "textwrap.TextWrapper",
        ),
        (
            "copyclass.py",
            "import copy\n\nclass P:\n    x: int\n\ndef f() -> int:\n    copy.copy(P())\n    return 1\n",
            "PS2001",
            "copy.copy",
        ),
        (
            "copynone.py",
            "import copy\n\ndef f() -> int:\n    copy.copy(None)  # type: ignore[arg-type]\n    return 1\n",
            "PS2001",
            "copy.copy",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert code in got.stderr
        assert text in got.stderr
