"""Closed surfaces past the queue: resources, hmac, unicodedata, tempfile, atexit, shutil."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, compile_run, oracle, run_cli, write_case


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_hmac_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "hmac_tok.py",
        "\n".join(
            [
                "import hashlib",
                "import hmac",
                "def dig() -> int:",
                "    digest: bytes = hmac.digest(b'key', b'msg', 'sha256')",
                "    other: bytes = hmac.new(b'key', b'msg', hashlib.sha256).digest()",
                "    if len(digest) != 32 or len(other) != 32:",
                "        return 0",
                "    if not hmac.compare_digest(digest, other):",
                "        return 0",
                "    wide: bytes = hmac.digest(b'key', b'msg', 'SHA512')",
                "    return digest[0] * 1000 + len(wide)",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "dig")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "dig")
    ll = tmp_path / "dig.ll"
    emitted = run_cli("compile", str(path), "--emit", "llvm", "-o", str(ll))
    assert emitted.returncode == 0, emitted.stderr
    assert "ps_island_" not in ll.read_text(encoding="utf-8")


def test_hmac_algorithm_stays_a_fence(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "hmac_bad.py",
        "\n".join(
            [
                "import hmac",
                "def f() -> bytes:",
                "    return hmac.digest(b'key', b'msg', 'sha3_256')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 3
    assert "PS2001" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_unicodedata_latin1_matches_cpython(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "udata.py",
        "\n".join(
            [
                "import unicodedata",
                "def probe() -> str:",
                "    cat: str = unicodedata.category('A') + unicodedata.category(' ')",
                "    form: str = unicodedata.normalize('NFKC', '\\u00b2')",
                "    named: str = unicodedata.name('A')",
                "    found: str = unicodedata.lookup('LATIN CAPITAL LETTER A')",
                "    parts: str = unicodedata.decomposition('\\u00e9')",
                "    return cat + form + found + named[0:6] + parts + unicodedata.unidata_version",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")
    ll = tmp_path / "udata.ll"
    emitted = run_cli("compile", str(path), "--emit", "llvm", "-o", str(ll))
    assert emitted.returncode == 0, emitted.stderr
    assert "ps_island_" not in ll.read_text(encoding="utf-8")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_unicodedata_numbers_and_flags_match(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "udata_num.py",
        "\n".join(
            [
                "import unicodedata",
                "def probe() -> int:",
                "    total: int = unicodedata.combining('e') + unicodedata.decimal('7') + unicodedata.digit('\\u00b2')",
                "    total = total + int(unicodedata.numeric('\\u00bd') * 100.0)",
                "    if unicodedata.is_normalized('NFC', '\\u00e9'):",
                "        total = total + 1",
                "    if unicodedata.mirrored('('):",
                "        total = total + 1",
                "    if unicodedata.bidirectional('A') == 'L':",
                "        total = total + 1",
                "    if unicodedata.east_asian_width('A') == 'Na':",
                "        total = total + 1",
                "    if unicodedata.name('\\x00', 'none') == 'none':",
                "        total = total + 1",
                "    return total",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_normalization_outside_latin1_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "nfd.py",
        "\n".join(
            [
                "import unicodedata",
                "def f() -> str:",
                "    return unicodedata.normalize('NFD', '\\u00e9')",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1
    assert "ValueError" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_resource_read_and_as_file_match(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "res.py",
        "\n".join(
            [
                "import importlib.resources",
                "def probe() -> str:",
                "    text: str = (",
                "        importlib.resources.files('pyscriptc')",
                "        .joinpath('runtime')",
                "        .joinpath('ps_rt.h')",
                "        .read_text(encoding='ascii')",
                "    )",
                "    shown: str = ''",
                "    with importlib.resources.as_file(",
                "        importlib.resources.files('pyscriptc').joinpath('runtime').joinpath('ps_rt.h')",
                "    ) as handle:",
                "        shown = str(handle)",
                "    if text[0:2] != '/*':",
                "        return 'bad'",
                "    if shown[0:1] != '/':",
                "        return 'bad'",
                "    return text[0:2]",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_missing_resource_package_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "miss.py",
        "\n".join(
            [
                "import importlib.resources",
                "def f() -> int:",
                "    importlib.resources.files('not_a_real_pkg_ps')",
                "    return 1",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "f")
    assert got.returncode == 1
    assert "ValueError" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_tempfile_and_shutil_match(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    tree = tmp_path / "tree"
    path = write_case(
        tmp_path,
        "files.py",
        "\n".join(
            [
                "import os",
                "import posixpath",
                "import shutil",
                "import tempfile",
                "def probe() -> str:",
                f"    src: str = {str(src)!r}",
                f"    dst: str = {str(dst)!r}",
                f"    tree: str = {str(tree)!r}",
                "    with open(src, 'w') as handle:",
                "        handle.write('hello')",
                "    copied: str = shutil.copyfile(src, dst)",
                "    with open(dst, 'r') as handle:",
                "        body: str = handle.read()",
                "    os.makedirs(tree + '/sub')",
                "    with open(tree + '/sub/a.txt', 'w') as handle:",
                "        handle.write('x')",
                "    shutil.rmtree(tree)",
                "    made: str = tempfile.mkdtemp()",
                "    if copied != dst or body != 'hello':",
                "        return 'bad'",
                "    if posixpath.exists(tree):",
                "        return 'bad'",
                "    if not posixpath.isdir(made):",
                "        return 'bad'",
                "    if not made.startswith(tempfile.gettempdir() + '/' + tempfile.gettempprefix()):",
                "        return 'bad'",
                "    shutil.rmtree(made)",
                "    return tempfile.gettempdir()",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "probe")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "probe")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_gettempdir_strips_trailing_slash(tmp_path: Path) -> None:
    """TMPDIR with a trailing slash (macOS launchd sets it) reads back normalized.

    CPython abspath-normalizes every tempdir candidate before returning it,
    so `gettempdir()` never carries the trailing slash. The runtime must do
    the same, or gettempdir() and mkdtemp() disagree with the oracle.
    """
    slash_tmp = tmp_path / "tslash"
    slash_tmp.mkdir()
    probe = write_case(
        tmp_path,
        "tempdir.py",
        "\n".join(
            [
                "import tempfile",
                "def probe() -> str:",
                "    made: str = tempfile.mkdtemp()",
                "    if not made.startswith(tempfile.gettempdir()):",
                "        return 'bad'",
                "    return tempfile.gettempdir()",
                "",
            ]
        ),
    )
    env = {**os.environ, "TMPDIR": str(slash_tmp) + "/"}
    got = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(probe),
            "--run",
            "--call",
            "probe",
            "-o",
            str(tmp_path / "p"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="latin-1",
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == str(slash_tmp) + "\n"
    assert not got.stdout.startswith(str(slash_tmp) + "/")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_atexit_runs_on_the_way_out(tmp_path: Path) -> None:
    marker = tmp_path / "bye.txt"
    path = write_case(
        tmp_path,
        "bye.py",
        "\n".join(
            [
                "import atexit",
                "def cleanup() -> None:",
                f"    with open({str(marker)!r}, 'w') as handle:",
                "        handle.write('bye')",
                "def arm() -> int:",
                "    atexit.register(cleanup)",
                "    return 7",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "arm")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert marker.read_text(encoding="utf-8") == "bye"
    marker.unlink()
    assert oracle(path, "arm") == "7\n"
    assert marker.read_text(encoding="utf-8") == "bye"


def test_small_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "ucd.py",
            "import unicodedata\n\ndef f() -> int:\n    unicodedata.ucd_3_2_0\n    return 1\n",
            "unicodedata.ucd_3_2_0",
        ),
        (
            "unreg.py",
            "import atexit\n\ndef f() -> int:\n    atexit.unregister(f)\n    return 1\n",
            "atexit.unregister",
        ),
    ]
    for name, source, needle in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli("compile", str(path), "--emit", "ir", "-o", str(tmp_path / (name + ".ir.json")))
        assert got.returncode == 3, got.stderr
        assert needle in got.stderr


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_certifi_where_matches_cpython(tmp_path: Path) -> None:
    src = ROOT / "benchmarks" / "compat" / "top100" / "src"
    core = src / "certifi" / "core.py"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(src)])}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(core),
            "--run",
            "--call",
            "where",
            "--args",
            "[]",
            "-o",
            str(tmp_path / "where"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    oracle_proc = subprocess.run(
        [sys.executable, "-c", "import certifi; print(certifi.where())"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == oracle_proc.stdout
    assert proc.stdout.endswith("cacert.pem\n")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_certifi_package_where_matches_cpython(tmp_path: Path) -> None:
    src = ROOT / "benchmarks" / "compat" / "top100" / "src"
    package = src / "certifi"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(src)])}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(package),
            "--skip-check",
            "--run",
            "--call",
            "certifi.core.where",
            "--args",
            "[]",
            "-o",
            str(tmp_path / "certifi-where"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "skipped certifi/__main__.py" in proc.stderr
    assert "not an importable module" in proc.stderr
    oracle_proc = subprocess.run(
        [sys.executable, "-c", "import certifi; print(certifi.where())"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == oracle_proc.stdout
    assert proc.stdout.endswith("cacert.pem\n")
