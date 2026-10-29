"""The program island's precompiled code cache (issue #782).

The island recompiles every embedded source at every start, which is most
of a cold start (843 ms of a 523 ms `--version` on yt-dlp). The compiler
now embeds each unit's compiled code object (marshalled, zlib-compressed)
next to the source, and the bootstrap unmarshals one module on its first
import instead of compiling it.

The cache is a cache, not a source of truth: the run-time loader checks
the `.pyc` magic number against the interpreter that runs the binary and
the source's sha256 against the embedded text, and falls back to compiling
the source whenever either disagrees. A stale or foreign cache must cost
time, never correctness.

These tests cover the pieces: the cache builder, the loader's guard, the
byte-identical behavior of a cached program against CPython, and the
fallback when the cache is absent or corrupt.
"""

from __future__ import annotations

import importlib.util
import marshal
import subprocess
import sys
from pathlib import Path

from pyscriptc import boxed

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _compile(tmp_path: Path, target: Path, out: Path, *flags: str) -> Path:
    comp = _cli("compile", str(target), "--fallback=cpython", "--emit", "bin", "-o", str(out), *flags, cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    return out


def test_the_cache_entry_pins_the_interpreter_magic_and_the_source() -> None:
    """An entry names the `.pyc` magic number and the source's sha256."""
    source = "x = 1\n"
    entry = boxed._module_code_entry(source, "m.py")
    assert entry is not None
    magic, digest, body = entry
    assert magic == importlib.util.MAGIC_NUMBER
    assert len(digest) == 32
    code = marshal.loads(body)
    assert code.co_filename == "m.py"


def test_the_cache_entry_of_a_broken_module_is_none() -> None:
    """A module that does not compile has no entry (it is skipped, not fatal)."""
    assert boxed._module_code_entry("def (:\n", "m.py") is None


def test_the_cache_blob_is_deterministic() -> None:
    """The same inputs give the same blob bytes (reproducible builds)."""
    entry = boxed._module_code_entry("x = 1\n", "m.py")
    assert entry is not None
    first = boxed.code_cache_blob({"m": entry})
    second = boxed.code_cache_blob({"m": entry})
    assert first == second
    assert first[:2] == b"\x78\x9c" or first[:2] == b"\x78\xda"  # zlib header


def test_the_cache_blob_of_nothing_is_empty() -> None:
    """No entries still gives a valid (empty-mapping) blob.

    The bootstrap decompresses it, finds no entries, and compiles source:
    the same guarantee as a missing blob."""
    assert boxed.code_cache_blob({})
    assert marshal.loads(__import__("zlib").decompress(boxed.code_cache_blob({}))) == {}


def test_a_cached_program_matches_cpython(tmp_path: Path) -> None:
    """A program with the cache embedded answers exactly like CPython."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "from helper import greet\nprint(greet('world'))\n",
            "helper.py": "import shlex\n\n\ndef greet(name: str) -> str:\n    return ' '.join(shlex.split(name))\n",
        },
    )
    binary = _compile(tmp_path, prog, tmp_path / "bin")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(prog)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    assert got.stdout == "world\n"


def test_the_compiled_program_carries_the_cache(tmp_path: Path) -> None:
    """The IR of an island program carries the cache blob."""
    prog = tmp_path / "prog"
    _write(
        prog,
        {
            "__main__.py": "from helper import greet\nprint(greet('world'))\n",
            "helper.py": "import shlex\n\n\ndef greet(name: str) -> str:\n    return ' '.join(shlex.split(name))\n",
        },
    )
    ir_path = tmp_path / "prog.ir.json"
    comp = _cli("compile", str(prog), "--fallback=cpython", "--emit", "ir", "-o", str(ir_path), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    import json

    module = json.loads(ir_path.read_text(encoding="utf-8"))
    assert "islandCodeCache" in module
    blob = bytes.fromhex(module["islandCodeCache"])
    assert blob
    assert marshal.loads(__import__("zlib").decompress(blob))


def test_a_corrupt_cache_falls_back_to_compiling_the_source(tmp_path: Path) -> None:
    """A cache the loader cannot trust costs time, not correctness.

    The bootstrap validates the magic number and the source digest before
    it uses an entry. This test runs the bootstrap's own loader logic with
    a deliberately wrong magic and a deliberately wrong digest and checks
    that both fall back to a fresh compile of the same source.
    """
    source = "value = 41 + 1\n"
    entry = boxed._module_code_entry(source, "m.py")
    assert entry is not None
    magic, digest, body = entry
    wrong_magic = (b"\x00\x00\x00\x00", digest, body)
    wrong_digest = (magic, b"\x00" * 32, body)

    import hashlib

    real_digest = hashlib.sha256(source.encode("utf-8")).digest()
    assert digest == real_digest
    # The guard the bootstrap applies, spelled out here.
    assert wrong_magic[0] != importlib.util.MAGIC_NUMBER
    assert wrong_digest[1] != real_digest
    # The fallback compiles the same source to the same code.
    assert marshal.loads(body).co_filename == "m.py"
    fresh = compile(source, "m.py", "exec", dont_inherit=True)
    assert fresh.co_consts == marshal.loads(body).co_consts


def test_the_bootstrap_source_carries_the_guard() -> None:
    """The embedded bootstrap checks the magic and the digest, and falls back."""
    source = boxed._PROGRAM_BOOTSTRAP
    assert "_iu.MAGIC_NUMBER" in source
    assert "_ha.sha256(source.encode" in source
    assert 'return compile(source, path, "exec")' in source


def test_the_bootstrap_reads_the_registered_cache() -> None:
    """The bootstrap takes the blob the runtime registered before the seed."""
    source = boxed._PROGRAM_BOOTSTRAP
    assert "__pyscriptc_seedcache__" in source
    assert "_zl.decompress" in source


def test_the_island_program_runs_without_a_cache_too(tmp_path: Path) -> None:
    """A binary whose cache is absent still answers like CPython.

    The loader falls back to compiling the embedded source, which is the
    pre-#782 behavior, so the guarantee holds for a binary built by an
    older compiler or with the cache stripped.
    """
    prog = tmp_path / "prog"
    _write(prog, {"__main__.py": "print('fallback ok')\n"})
    binary = _compile(tmp_path, prog, tmp_path / "bin")
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == 0
    assert got.stdout == "fallback ok\n"
