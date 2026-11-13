"""uuid: the closed subset.

`uuid4`, `uuid1`, `uuid5` and the `UUID` class's canonical text lower
natively. The deterministic rows (`uuid5` over a fixed namespace, and `UUID`
over a fixed text) diff against the CPython oracle byte for byte. The random
rows (`uuid4`, `uuid1`) are checked for shape, version and variant bits
instead: their values differ from CPython's by construction.
"""

from __future__ import annotations

import re
import uuid as py_uuid
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

V4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
V1_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-1[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_uuid5_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import uuid",
            "",
            "def run() -> int:",
            "    ns = uuid.UUID('36dd619a-56dc-595b-9e09-37f4152c7b5d')",
            "    print(uuid.uuid5(ns, 'video'))",
            "    print(uuid.uuid5(ns, 'media'))",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "uuid5.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    expected = "\n".join(
        [
            str(py_uuid.uuid5(py_uuid.UUID("36dd619a-56dc-595b-9e09-37f4152c7b5d"), "video")),
            str(py_uuid.uuid5(py_uuid.UUID("36dd619a-56dc-595b-9e09-37f4152c7b5d"), "media")),
        ]
    )
    assert got.stdout == expected + "\n0\n", got.stdout


def test_uuid_parse_matches_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import uuid",
            "",
            "def run() -> int:",
            "    print(uuid.UUID('36dd619a-56dc-595b-9e09-37f4152c7b5d'))",
            "    print(uuid.UUID('00000000-0000-0000-0000-000000000000'))",
            "    print(uuid.UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "parse.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    expected = "\n".join(
        [
            str(py_uuid.UUID("36dd619a-56dc-595b-9e09-37f4152c7b5d")),
            str(py_uuid.UUID("00000000-0000-0000-0000-000000000000")),
            str(py_uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")),
        ]
    )
    assert got.stdout == expected + "\n0\n", got.stdout


def test_uuid4_shape(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "uuid4.py",
        "\n".join(
            [
                "import uuid",
                "",
                "def run() -> int:",
                "    print(uuid.uuid4())",
                "    print(uuid.uuid4().hex)",
                "    return 0",
                "",
            ]
        ),
    )
    lines = out.splitlines()
    assert len(lines) == 3, out
    assert V4_RE.match(lines[0]), lines[0]
    assert re.match(r"^[0-9a-f]{32}$", lines[1]), lines[1]


def test_uuid1_shape(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "uuid1.py",
        "\n".join(
            [
                "import uuid",
                "",
                "def run() -> int:",
                "    print(uuid.uuid1())",
                "    return 0",
                "",
            ]
        ),
    )
    lines = out.splitlines()
    assert len(lines) == 2, out
    assert V1_RE.match(lines[0]), lines[0]


def test_uuid4_differs_between_calls(tmp_path: Path) -> None:
    out = _run(
        tmp_path,
        "distinct.py",
        "\n".join(
            [
                "import uuid",
                "",
                "def run() -> int:",
                "    a = uuid.uuid4()",
                "    b = uuid.uuid4()",
                "    print(a != b)",
                "    return 0",
                "",
            ]
        ),
    )
    assert out == "True\n0\n", out


def test_uuid5_over_a_uuid5_result(tmp_path: Path) -> None:
    """`uuid5` takes the canonical text of a UUID, so a nested call lowers."""
    source = "\n".join(
        [
            "import uuid",
            "",
            "def run() -> int:",
            "    ns = uuid.UUID('36dd619a-56dc-595b-9e09-37f4152c7b5d')",
            "    print(uuid.uuid5(uuid.uuid5(ns, 'video'), 'media'))",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "nested.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    expected = str(
        py_uuid.uuid5(
            py_uuid.uuid5(py_uuid.UUID("36dd619a-56dc-595b-9e09-37f4152c7b5d"), "video"),
            "media",
        )
    )
    assert got.stdout == expected + "\n0\n", got.stdout


def test_uuid_import_is_not_fenced(tmp_path: Path) -> None:
    """`import uuid` binds the module: the import itself must not fence."""
    path = write_case(
        tmp_path,
        "import_only.py",
        "\n".join(
            [
                "import uuid",
                "",
                "def run() -> int:",
                "    print(uuid.uuid5(uuid.UUID('36dd619a-56dc-595b-9e09-37f4152c7b5d'), 'video'))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert oracle(path, "run").strip() == got.stdout.strip()


def test_uuid_rejects_the_refused_forms(tmp_path: Path) -> None:
    """The `.int`, `.urn` and `.bytes` forms stay refused."""
    path = write_case(
        tmp_path,
        "refused.py",
        "\n".join(
            [
                "import uuid",
                "",
                "def run() -> int:",
                "    print(uuid.uuid4().int)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS" in got.stderr, got.stderr
