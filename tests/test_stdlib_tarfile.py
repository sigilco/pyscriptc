"""tarfile and zipfile: the archive read surface.

The member lists, the member bytes and the extraction diff against the
CPython oracle; the archives themselves are built by the host's tarfile and
zipfile before the compiled program reads them. The write modes fence with
a named message instead of reaching the runtime, because CPython would
create or truncate an archive the binary never writes.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile as py_tarfile
import zipfile as py_zipfile
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source

_PAYLOAD = "hello archive\n"


def _build(tmp_path: Path) -> dict[str, str]:
    """The archives the tests read: a gzipped tar, a plain tar, a deflated
    zip and a stored zip, each carrying one file and one nested file."""
    plain_dir = tmp_path / "src"
    plain_dir.mkdir(exist_ok=True)
    (plain_dir / "hello.txt").write_text(_PAYLOAD, encoding="utf-8")
    (plain_dir / "docs").mkdir(exist_ok=True)
    (plain_dir / "docs" / "readme.txt").write_text("nested\n", encoding="utf-8")
    paths: dict[str, str] = {}
    gz = tmp_path / "plain.tar.gz"
    with py_tarfile.open(gz, "w:gz") as t:
        t.add(plain_dir / "hello.txt", arcname="hello.txt")
        t.add(plain_dir / "docs", arcname="docs")
    paths["gz"] = str(gz)
    plain = tmp_path / "plain.tar"
    with py_tarfile.open(plain, "w") as t:
        t.add(plain_dir / "hello.txt", arcname="hello.txt")
    paths["tar"] = str(plain)
    deflate = tmp_path / "deflate.zip"
    with py_zipfile.ZipFile(deflate, "w", py_zipfile.ZIP_DEFLATED) as z:
        z.write(plain_dir / "hello.txt", arcname="hello.txt")
        z.write(plain_dir / "docs" / "readme.txt", arcname="docs/readme.txt")
    paths["zip"] = str(deflate)
    stored = tmp_path / "stored.zip"
    with py_zipfile.ZipFile(stored, "w", py_zipfile.ZIP_STORED) as z:
        z.write(plain_dir / "hello.txt", arcname="hello.txt")
    paths["stored"] = str(stored)
    return paths


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    """The compiled output must equal the CPython oracle's output."""
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_tarfile_gz_reads_match_cpython(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    _oracle(
        tmp_path,
        "tar_gz.py",
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                f'    with tarfile.open({paths["gz"]!r}, "r:gz") as t:',
                "        names: list[str] = t.getnames()",
                "        print(sorted(names))",
                "        f = t.extractfile('hello.txt')",
                "        if f is not None:",
                "            data: bytes = f.read()",
                "            print(data)",
                "        nested_f = t.extractfile('docs/readme.txt')",
                "        if nested_f is not None:",
                "            nested: bytes = nested_f.read()",
                "            print(nested)",
                "    return 0",
                "",
            ]
        ),
    )


def test_tarfile_plain_read_matches_cpython(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    _oracle(
        tmp_path,
        "tar_plain.py",
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                f'    with tarfile.open({paths["tar"]!r}, "r") as t:',
                "        names: list[str] = t.getnames()",
                "        print(names)",
                "        f = t.extractfile('hello.txt')",
                "        if f is not None:",
                "            data: bytes = f.read()",
                "            print(data)",
                "    return 0",
                "",
            ]
        ),
    )


def test_zipfile_read_matches_cpython(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    for key in ["zip", "stored"]:
        _oracle(
            tmp_path,
            f"zip_{key}.py",
            "\n".join(
                [
                    "import zipfile",
                    "",
                    "def run() -> int:",
                    f"    with zipfile.ZipFile({paths[key]!r}) as z:",
                    "        names: list[str] = z.namelist()",
                    "        print(sorted(names))",
                    "        data: bytes = z.read('hello.txt')",
                    "        print(data)",
                    "    return 0",
                    "",
                ]
            ),
        )


def test_zipfile_mode_keyword(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    _oracle(
        tmp_path,
        "zip_mode.py",
        "\n".join(
            [
                "import zipfile",
                "",
                "def run() -> int:",
                f'    with zipfile.ZipFile({paths["zip"]!r}, "r") as z:',
                "        data: bytes = z.read('hello.txt')",
                "        print(data)",
                "    return 0",
                "",
            ]
        ),
    )


def test_extractall_writes_the_members(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _oracle(
        tmp_path,
        "extract.py",
        "\n".join(
            [
                "import os",
                "import tarfile",
                "import zipfile",
                "",
                "def run() -> int:",
                f'    with tarfile.open({paths["gz"]!r}, "r:gz") as t:',
                f"        t.extractall({str(out_dir / 'tar')!r})",
                f"    with zipfile.ZipFile({paths['zip']!r}) as z:",
                f"        z.extractall({str(out_dir / 'zip')!r})",
                "    print(os.path.isfile(os.path.join(",
                f"        {str(out_dir / 'tar')!r}, 'hello.txt')))",
                "    with open(os.path.join(",
                f"        {str(out_dir / 'tar')!r}, 'hello.txt'), 'r') as handle:",
                "        print(handle.read())",
                "    print(os.path.isfile(os.path.join(",
                f"        {str(out_dir / 'zip')!r}, 'docs', 'readme.txt')))",
                "    with open(os.path.join(",
                f"        {str(out_dir / 'zip')!r}, 'docs', 'readme.txt'), 'r') as handle:",
                "        print(handle.read())",
                "    return 0",
                "",
            ]
        ),
    )


def test_annotated_handles_lower(tmp_path: Path) -> None:
    paths = _build(tmp_path)
    _oracle(
        tmp_path,
        "annotated.py",
        "\n".join(
            [
                "import tarfile",
                "import zipfile",
                "",
                "def run() -> int:",
                f'    t: tarfile.TarFile = tarfile.open({paths["gz"]!r}, "r:gz")',
                "    names: list[str] = t.getnames()",
                "    print(names)",
                f"    z: zipfile.ZipFile = zipfile.ZipFile({paths['zip']!r})",
                "    zn: list[str] = z.namelist()",
                "    print(zn)",
                "    t.close()",
                "    z.close()",
                "    return 0",
                "",
            ]
        ),
    )


def test_extractfile_of_a_directory_member_is_none(tmp_path: Path) -> None:
    """CPython's `extractfile` answers None for a directory member."""
    paths = _build(tmp_path)
    _oracle(
        tmp_path,
        "directory.py",
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                f'    with tarfile.open({paths["gz"]!r}, "r:gz") as t:',
                "        d = t.extractfile('docs')",
                "        print(d is None)",
                "    return 0",
                "",
            ]
        ),
    )


def test_extractfile_of_a_missing_name_raises(tmp_path: Path) -> None:
    """CPython's `extractfile` raises KeyError for a name no member carries.

    The compiled program and the CPython oracle both raise, so the
    comparison is on the exception type and not on the stdout."""
    paths = _build(tmp_path)
    source = "\n".join(
        [
            "import tarfile",
            "",
            "def run() -> int:",
            f'    with tarfile.open({paths["gz"]!r}, "r:gz") as t:',
            "        f = t.extractfile('nope.txt')",
            "        print(f is None)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "missing.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    assert "KeyError" in got.stderr, got.stderr
    oracle_path = write_case(tmp_path, "missing_oracle.py", source + "\nrun()\n")
    proc = subprocess.run([sys.executable, str(oracle_path)], capture_output=True, text=True, encoding="latin-1")
    assert proc.returncode != 0
    assert "KeyError" in proc.stderr, proc.stderr


def test_write_modes_fence() -> None:
    _fence(
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                "    with tarfile.open('/tmp/x.tar.gz', 'w:gz') as t:",
                "        pass",
                "",
            ]
        ),
        "PS2001",
        "w:gz",
    )
    _fence(
        "\n".join(
            [
                "import zipfile",
                "",
                "def run() -> int:",
                "    with zipfile.ZipFile('/tmp/x.zip', 'w') as z:",
                "        pass",
                "",
            ]
        ),
        "PS2001",
        "read-only",
    )


def test_unknown_modes_fence() -> None:
    _fence(
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                "    with tarfile.open('/tmp/x.tar', 'r:bzt') as t:",
                "        pass",
                "",
            ]
        ),
        "PS2001",
        "r, r:, r:gz, r:bz2, r:xz",
    )
    _fence(
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                "    mode: str = 'r'",
                "    t = tarfile.open('/tmp/x.tar', mode)",
                "    print(t)",
                "",
            ]
        ),
        "PS1003",
        "string literal",
    )


def test_unlowered_members_fence() -> None:
    _fence(
        "\n".join(
            [
                "import tarfile",
                "",
                "def run() -> int:",
                "    with tarfile.open('/tmp/x.tar', 'r') as t:",
                "        info = t.getmember('hello.txt')",
                "        print(info)",
                "",
            ]
        ),
        "PS2001",
        "TarFile.getmember is not lowered",
    )
