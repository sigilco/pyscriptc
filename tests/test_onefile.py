"""The onefile output: one executable that extracts its payload on first run.

The onefile is the `files_n` lever of the yt-dlp composite: a bundle is a
153 file tree, a onefile is one file. The stub finds the payload appended to
its own file, extracts it into the cache directory named by the payload's
sha256 on the first run and execs the extracted binary on every run.

These tests build a onefile of a tiny bundle tree (a shell script standing in
for the compiled binary) and check the payload format, the cold and warm
runs, the cache keying and the refusal of a payload-less file.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from pathlib import Path

import pytest

from pyscriptc import onefile

pytestmark = pytest.mark.skipif(
    os.uname().sysname == "Darwin",
    reason="the onefile stub reads /proc/self/exe (Linux only for now)",
)


def _bundle(tmp_path: Path, name: str = "prog") -> Path:
    """A minimal bundle tree: `bin/<name>` plus a `lib/` beside it."""
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    binary = root / "bin" / name
    binary.write_text('#!/bin/sh\necho "payload says $1"\n', encoding="utf-8")
    binary.chmod(0o755)
    (root / "lib" / "data.txt").write_text("sidecar\n", encoding="utf-8")
    return root


def _payload_of(one: Path) -> tuple[bytes, str]:
    """The payload bytes and the digest the footer of a onefile carries."""
    data = one.read_bytes()
    assert data.endswith(onefile.MAGIC)
    footer = data[-onefile.FOOTER :]
    digest = footer[:64].decode("ascii")
    size = struct.unpack("<Q", footer[65:73])[0]
    return data[-onefile.FOOTER - size : -onefile.FOOTER], digest


def test_the_onefile_is_one_file(tmp_path: Path) -> None:
    """The whole bundle travels in one executable."""
    bundle = _bundle(tmp_path)
    out = tmp_path / "prog.onefile"
    onefile.build(bundle, "prog", out)
    assert out.is_file()
    payload, digest = _payload_of(out)
    assert hashlib.sha256(payload).hexdigest() == digest
    assert len(payload) > 0


def test_the_first_run_extracts_and_the_second_reuses(tmp_path: Path) -> None:
    """The cold run extracts into the cache; the warm run reuses it."""
    bundle = _bundle(tmp_path)
    out = tmp_path / "prog.onefile"
    onefile.build(bundle, "prog", out)
    cache = tmp_path / "cache"
    env = {**os.environ, "PYSC_ONEFILE_CACHE": str(cache)}

    cold = subprocess.run([str(out), "one"], capture_output=True, text=True, env=env)
    assert cold.returncode == 0, cold.stderr
    assert "payload says one" in cold.stdout

    _payload, digest = _payload_of(out)
    extracted = cache / digest
    assert (extracted / "bin" / "prog").is_file()
    assert (extracted / "lib" / "data.txt").read_text(encoding="utf-8") == "sidecar\n"

    # The warm run reuses the tree: the extracted file's mtime does not move.
    marker = extracted / "bin" / "prog"
    before = marker.stat().st_mtime_ns
    warm = subprocess.run([str(out), "two"], capture_output=True, text=True, env=env)
    assert warm.returncode == 0, warm.stderr
    assert "payload says two" in warm.stdout
    assert marker.stat().st_mtime_ns == before


def test_the_cache_key_is_the_payload_digest(tmp_path: Path) -> None:
    """A different payload extracts beside the old tree, not over it."""
    bundle = _bundle(tmp_path)
    out = tmp_path / "prog.onefile"
    onefile.build(bundle, "prog", out)
    cache = tmp_path / "cache"
    env = {**os.environ, "PYSC_ONEFILE_CACHE": str(cache)}
    subprocess.run([str(out)], capture_output=True, text=True, env=env)
    first = sorted(p.name for p in cache.iterdir())

    (bundle / "lib" / "data.txt").write_text("changed\n", encoding="utf-8")
    onefile.build(bundle, "prog", out)
    subprocess.run([str(out)], capture_output=True, text=True, env=env)
    second = sorted(p.name for p in cache.iterdir())
    assert len(second) == len(first) + 1


def test_a_file_without_a_payload_is_refused(tmp_path: Path) -> None:
    """A plain binary with no payload names itself and exits non-zero."""
    stub = onefile._compile_stub("prog", tmp_path)
    done = subprocess.run([str(stub)], capture_output=True, text=True)
    assert done.returncode != 0
    assert "no payload" in done.stderr


def test_the_stub_needs_no_cc_at_run_time(tmp_path: Path) -> None:
    """The stub runs with an empty PATH except for its own shell script."""
    bundle = _bundle(tmp_path)
    out = tmp_path / "prog.onefile"
    onefile.build(bundle, "prog", out)
    cache = tmp_path / "cache"
    env = {"PYSC_ONEFILE_CACHE": str(cache), "PATH": "/nonexistent"}
    done = subprocess.run([str(out)], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert "payload says" in done.stdout


def test_a_missing_cc_is_a_refusal_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No C compiler on PATH names the remedy in one line."""
    bundle = _bundle(tmp_path)
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(onefile.OnefileError) as excinfo:
        onefile.build(bundle, "prog", tmp_path / "prog.onefile")
    assert "C compiler" in str(excinfo.value)
