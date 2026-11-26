# SPDX-License-Identifier: Apache-2.0
"""The data formats over code points (issue #477).

`json`, `orjson`, `csv`, `tomllib` and `yaml` all carry `str` values, so
they work on code points under the str model. Each case compiles with the
worktree's own `pyscriptc`, runs the native binary, and byte-compares
stdout, stderr and the exit code against the pinned CPython 3.14
interpreter running the same source.

The four formats that ride the standard library (`json`, `csv`, `tomllib`
and the `orjson` pack) compare against CPython 3.14. `yaml` compares
against PyYAML, which the pinned oracle does not ship, so its case is
marked `linux_container` and runs in the container that carries PyYAML.
"""

from __future__ import annotations

import shutil
import sys

import pytest
from tests.test_gates import ROOT, write_case
from tests.test_runtime_divergences import compile_and_run, oracle_run

ZIG = shutil.which("zig") is not None


def _check(tmp_path, name: str, source: str) -> None:
    """Compile `source`, run it and the pinned oracle, compare the bytes."""
    got = compile_and_run(tmp_path, name + ".py", source)
    want = oracle_run(tmp_path, name + ".py", source)
    assert got == want


# --- json: the escape table ------------------------------------------------

# The C0 control range and DEL. CPython escapes them as \u00XX in both
# modes; the default escapes DEL, the raw form keeps it as the byte it is.
JSON_CONTROL_ESCAPES = """
import json

for code in [0x00, 0x01, 0x08, 0x0B, 0x0C, 0x1F, 0x7F]:
    ch = chr(code)
    print(json.dumps(ch))
    print(json.dumps(ch, ensure_ascii=False))
"""

# The boundary code points of the UTF-8 widths: Latin-1, the two- and
# three-byte leads, the BMP's end and the two surrogate-pair halves.
JSON_WIDTH_BOUNDARIES = """
import json

for code in [0x20, 0x7E, 0x80, 0xE9, 0x7FF, 0x800, 0xFFFF, 0x10000, 0x10FFFF]:
    ch = chr(code)
    print(json.dumps(ch))
    print(json.dumps(ch, ensure_ascii=False))
"""

# The two-character escapes and the short forms.
JSON_SHORT_ESCAPES = """
import json

for text in ["a\\"b", "a\\\\b", "a/b", "a\\nb", "a\\rb", "a\\tb", "a\\x08b", "a\\x0cb"]:
    print(json.dumps(text))
    print(json.dumps(text, ensure_ascii=False))
"""

# The default and the raw form carry the same text; the reader takes both
# back and the code points count as code points.
JSON_ROUND_TRIP = """
import json

escaped: str = json.dumps("café")
raw: str = json.dumps("café", ensure_ascii=False)
print(escaped)
print(raw)
back_escaped: str = json.loads(escaped)
back_raw: str = json.loads(raw)
print(back_escaped == back_raw)
print(len(back_raw))

pair: str = json.dumps("😀")
print(pair)
print(json.dumps("😀", ensure_ascii=False))
back_pair: str = json.loads(pair)
print(back_pair)
"""

# The reader takes the raw UTF-8 form and the escaped form to the same text.
JSON_READER_BOTH_FORMS = """
import json

raw_doc: dict[str, str] = json.loads('{"k": "café", "j": "😀", "z": "中文"}')
escaped_doc: dict[str, str] = json.loads('{"k": "caf\\\\u00e9", "j": "\\\\ud83d\\\\ude00", "z": "\\\\u4e2d"}')
print(raw_doc["k"])
print(raw_doc["j"])
print(raw_doc["z"])
print(escaped_doc["k"])
print(escaped_doc["j"])
print(escaped_doc["z"])
print(raw_doc["k"] == escaped_doc["k"])
print(raw_doc["j"] == escaped_doc["j"])
print(len(raw_doc["j"]))
"""

# A bytes document decodes as UTF-8 first, so a malformed sequence raises
# the UnicodeDecodeError family like CPython's decode.
JSON_BYTES_INPUT = """
import json

d: dict[str, str] = json.loads(b'{"k": "caf\\xc3\\xa9"}')
print(d["k"])
try:
    bad: dict[str, str] = json.loads(b'{"k": "caf\\xe9"}')
    print("no error", bad)
except UnicodeDecodeError:
    print("decode error")
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
@pytest.mark.parametrize(
    "name, source",
    sorted(
        {
            "json_control_escapes": JSON_CONTROL_ESCAPES,
            "json_width_boundaries": JSON_WIDTH_BOUNDARIES,
            "json_short_escapes": JSON_SHORT_ESCAPES,
            "json_round_trip": JSON_ROUND_TRIP,
            "json_reader_both_forms": JSON_READER_BOTH_FORMS,
            "json_bytes_input": JSON_BYTES_INPUT,
        }.items()
    ),
)
def test_json_matches_cpython(tmp_path, name: str, source: str) -> None:
    _check(tmp_path, name, source)


# --- csv -------------------------------------------------------------------

# A non-ASCII field and a field with a comma, a quote and a newline in it:
# the excel dialect quotes the last three and leaves the first as the
# UTF-8 it is.
CSV_DIALECT = """
import csv
import io


def main() -> None:
    buf: io.StringIO = io.StringIO()
    writer: csv.DictWriter = csv.DictWriter(buf, ["name", "note", "city", "emoji"])
    writer.writeheader()
    writer.writerow({"name": "café", "note": "x,y", "city": "line\\nbreak", "emoji": "😀"})
    writer.writerow({"name": "naïve", "note": 'q"z', "city": "plain", "emoji": "中"})
    print(repr(buf.getvalue()))

    rows: list[dict[str, str | None]] = csv.DictReader(io.StringIO(buf.getvalue()))
    for row in rows:
        name: str | None = row["name"]
        note: str | None = row["note"]
        city: str | None = row["city"]
        emoji: str | None = row["emoji"]
        if name is None or note is None or city is None or emoji is None:
            print("missing")
        else:
            print(name)
            print(note)
            print(city)
            print(emoji)


main()
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_csv_matches_cpython(tmp_path) -> None:
    _check(tmp_path, "csv_dialect", CSV_DIALECT)


# --- tomllib ---------------------------------------------------------------

# The basic-string escapes name a code point and decode to it; the raw text
# of a basic string keeps its non-ASCII code points as they are.
TOML_STRING_ESCAPES = """
import tomllib


def main() -> None:
    d: dict[str, str] = tomllib.loads(
        'a = "\\\\u00e9"\\n'
        'b = "\\\\U0001f600"\\n'
        'c = "\\\\u4e2d"\\n'
        'd = "caf\\\\u00e9"\\n'
        'e = "raw é"\\n'
        'f = "\\\\u007f"\\n'
    )
    print(d["a"])
    print(d["b"])
    print(d["c"])
    print(d["d"])
    print(d["e"])
    print(d["f"])

    multi: dict[str, str] = tomllib.loads('m = \"\"\"\\nmulti é\\nsecond\\n\"\"\"\\n')
    print(multi["m"])


main()
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_tomllib_matches_cpython(tmp_path) -> None:
    _check(tmp_path, "toml_string_escapes", TOML_STRING_ESCAPES)


# --- yaml ------------------------------------------------------------------

# The double-quoted scalar's \x, \u and \U escapes name a code point and
# decode to it, so a code point above Latin-1 decodes the way PyYAML
# decodes it.
YAML_STRING_ESCAPES = """
import yaml


def main() -> None:
    d: dict[str, str] = yaml.safe_load(
        'a: "\\\\u00e9"\\n'
        'b: "\\\\U0001f600"\\n'
        'c: "\\\\u4e2d"\\n'
        'd: "\\\\x41"\\n'
        'e: "\\\\x80"\\n'
        'f: "\\\\xff"\\n'
        'g: raw é\\n'
        'h: "quoted é"\\n'
    )
    print(d["a"])
    print(d["b"])
    print(d["c"])
    print(d["d"])
    print(d["e"])
    print(d["f"])
    print(d["g"])
    print(d["h"])


main()
"""


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_yaml_matches_pyyaml(tmp_path) -> None:
    """The YAML escapes ride PyYAML, not the pinned CPython oracle.

    The container carries PyYAML, so the oracle there is the library
    itself and the comparison is the same byte comparison.
    """
    import subprocess

    path = write_case(tmp_path, "yaml_string_escapes.py", YAML_STRING_ESCAPES)
    want = subprocess.run(
        [sys.executable, str(path)], capture_output=True, timeout=120, cwd=tmp_path, check=True
    )
    got = compile_and_run(tmp_path, "yaml_string_escapes.py", YAML_STRING_ESCAPES)
    assert got == (want.stdout, want.stderr, want.returncode)


# --- orjson ----------------------------------------------------------------

# orjson never escapes the non-ASCII text: it emits the UTF-8 the code
# points are, the opposite of the json default.
ORJSON_RAW_UTF8 = """
import orjson


def main() -> None:
    data: bytes = orjson.dumps({"k": "café", "j": "😀"})
    print(data.decode())

    d: dict[str, str] = orjson.loads('{"k": "caf\\\\u00e9", "j": "\\\\ud83d\\\\ude00"}')
    print(d["k"])
    print(d["j"])

    round_trip: bytes = orjson.dumps(d)
    print(round_trip.decode())


main()
"""


@pytest.mark.skipif(not ZIG, reason="zig not on PATH")
def test_orjson_pack_writes_the_raw_utf8(tmp_path) -> None:
    """The orjson pack's own spelling: the raw UTF-8 the library writes.

    orjson is not vendored in the compat corpus, so the reference is the
    documented behaviour of the library: `dumps` returns bytes with the
    non-ASCII text raw. The pack's own test file
    (`tests/test_orjson_pack.py`) carries the library comparison in the
    container that has orjson installed.
    """
    import subprocess

    path = write_case(tmp_path, "orjson_raw_utf8.py", ORJSON_RAW_UTF8)
    want = subprocess.run(
        [sys.executable, str(path)], capture_output=True, timeout=120, cwd=tmp_path, check=True
    )
    got = compile_and_run(tmp_path, "orjson_raw_utf8.py", ORJSON_RAW_UTF8)
    assert got == (want.stdout, want.stderr, want.returncode)
