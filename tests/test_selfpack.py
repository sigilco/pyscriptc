"""The self-built compiler is a self-contained single binary (issue #814).

`pyscriptc compile src/pyscriptc --program --fallback=cpython --emit bin`
used to produce a binary that only worked next to the source tree: the
package data the compiler reads at run time (the runtime C sources the
emitter links, `target.json`, `native_packages.json`, the vendored
typeshed) stayed in `src/pyscriptc/`, and a sibling `pyscriptc-data/`
tree was staged beside the output.

The selfpack embeds that data in the binary and materializes it once
under the cache directory, so the output is one file that compiles hello
world from a clean environment. These tests pin that receipt: the binary
exists, carries no sibling tree, compiles a hello world with the source
tree unreadable, and the payload round-trips byte-for-byte.
"""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pyscriptc"

HELLO = """\
import sys


def main() -> int:
    print("hello from the self-packed compiler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

needs_cc = pytest.mark.skipif(
    not (ROOT / "src" / "pyscriptc" / "runtime").exists(),
    reason="runtime sources required",
)


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=cwd, capture_output=True, text=True, timeout=1800
    )


@needs_cc
def test_selfpack_payload_covers_the_package_data(tmp_path: Path) -> None:
    """The embedded payload carries every data file the compiler reads.

    The list is the contract: a file that is not in it and is not Python
    source is a file the compiled compiler cannot reach.
    """

    from pyscriptc.selfpack import DATA_FILES, DATA_ROOTS, build_payload, collect_package_data

    payload = collect_package_data(SRC)
    names = {name for name, _body in payload}
    for name in DATA_FILES:
        assert name in names, f"{name} is not in the selfpack payload"
    for root in DATA_ROOTS:
        assert any(name.startswith(root + "/") for name in names), f"{root} is not in the payload"
    # The runtime sources the emitter links beside every program are the ones
    # that matter most: without them the binary cannot compile anything.
    assert "runtime/ps_rt.c" in names
    assert "runtime/ps_rt.h" in names

    encoded = build_payload(payload)
    raw = __import__("base64").b64decode(encoded)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert set(archive.namelist()) == names


@needs_cc
def test_selfpack_extracts_byte_for_byte(tmp_path: Path) -> None:
    """The extracted tree holds the exact compile-time bytes."""

    from pyscriptc.selfpack import build_payload, collect_package_data, extract_payload

    payload = collect_package_data(SRC)
    encoded = build_payload(payload)
    target = tmp_path / "tree"
    extract_payload(encoded, target)
    for name, body in payload:
        assert (target / name).read_bytes() == body, name
    # A second extraction is a no-op (the marker short-circuits it).
    extract_payload(encoded, target)
    assert (target / "target.json").is_file()


@needs_cc
def test_the_self_built_compiler_is_one_file_that_compiles_hello(tmp_path: Path) -> None:
    """The self-built binary is a single file that compiles hello world.

    The compile runs with a stripped environment (no venv, no PYTHONPATH)
    and from a directory that holds nothing but the source file, so the
    binary cannot be leaning on the source tree or the caller's shell.
    """

    binary = tmp_path / "selfc"
    comp = _cli(
        "compile",
        str(SRC),
        "--program",
        "--fallback=cpython",
        "--skip-check",
        "--emit",
        "bin",
        "-o",
        str(binary),
        cwd=ROOT,
    )
    assert comp.returncode == 0, comp.stderr[-4000:]
    assert binary.is_file()

    # One file: no sibling data tree is staged beside the output.
    siblings = sorted(p.name for p in tmp_path.iterdir() if p.name != "selfc")
    assert "pyscriptc-data" not in siblings, siblings

    work = tmp_path / "work"
    work.mkdir()
    hello = work / "hello.py"
    hello.write_text(HELLO, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(work)}
    built = subprocess.run(
        [str(binary), "compile", str(hello), "--skip-check", "--emit", "bin", "-o", str(work / "hello")],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=work,
        env=env,
    )
    assert built.returncode == 0, built.stderr[-4000:]
    ran = subprocess.run([str(work / "hello")], capture_output=True, text=True, timeout=120, cwd=work, env=env)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == "hello from the self-packed compiler\n"

    # The extracted payload is the compiler's own data, under the cache
    # directory the stripped environment points at.
    extracted = list((work / ".cache" / "pyscriptc" / "selfpack").glob("*/"))
    assert extracted, "the selfpack did not materialize its payload"
    assert (extracted[0] / "runtime" / "ps_rt.c").is_file()
    assert (extracted[0] / "target.json").is_file()
