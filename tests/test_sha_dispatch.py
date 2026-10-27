"""Differential verification for SHA-256 and SHA-512 hardware dispatch and fallback.

Verifies native execution against the CPython hashlib oracle across empty, short,
block-boundary, and large inputs for both the hardware accelerated path and the
portable fallback (PYSC_SHA_PORTABLE=1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pyscriptc import cli

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

DIFFERENTIAL_SOURCE = """
import hashlib
import hmac

def test_hashes() -> int:
    # Empty, short, block-boundary, and large block inputs
    sizes: list[int] = [
        0, 1, 2, 10,
        55, 56, 57, 63, 64, 65,
        111, 112, 113, 119, 120, 121, 127, 128, 129,
        239, 240, 241, 255, 256, 257,
        1024, 4096, 8192, 65536,
    ]
    for s in sizes:
        data: bytes = b"K" * s
        h256_hex: str = hashlib.sha256(data).hexdigest()
        h256_raw: bytes = hashlib.sha256(data).digest()
        h512_hex: str = hashlib.sha512(data).hexdigest()
        h512_raw: bytes = hashlib.sha512(data).digest()
        h384_hex: str = hashlib.sha384(data).hexdigest()
        h384_raw: bytes = hashlib.sha384(data).digest()
        print(s, len(h256_raw), h256_hex[:8], len(h512_raw), h512_hex[:8], len(h384_raw), h384_hex[:8])

    # Distinct non-repeating data patterns
    msg: bytes = b"The quick brown fox jumps over the lazy dog."
    print("fox256:", hashlib.sha256(msg).hexdigest())
    print("fox512:", hashlib.sha512(msg).hexdigest())
    print("fox384:", hashlib.sha384(msg).hexdigest())

    # HMAC integration with sha256 and sha512
    key: bytes = b"secret-key-12345"
    h256_hmac: bytes = hmac.new(key, msg, hashlib.sha256).digest()
    h512_hmac: bytes = hmac.new(key, msg, hashlib.sha512).digest()
    print("hmac256:", len(h256_hmac), hmac.digest(key, msg, "sha256") == h256_hmac)
    print("hmac512:", len(h512_hmac), hmac.digest(key, msg, "sha512") == h512_hmac)
    return 0

test_hashes()
"""


@needs_cc
def test_sha_differential_against_hashlib(tmp_path: Path) -> None:
    source = tmp_path / "test_sha_diff.py"
    source.write_text(DIFFERENTIAL_SOURCE, encoding="utf-8")
    code = cli.main(["verify", str(source), "--skip-check"])
    assert code == 0, "Hardware path diverged from oracle"


@needs_cc
def test_sha_portable_fallback_differential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYSC_SHA_PORTABLE", "1")
    source = tmp_path / "test_sha_portable.py"
    source.write_text(DIFFERENTIAL_SOURCE, encoding="utf-8")
    code = cli.main(["verify", str(source), "--skip-check"])
    assert code == 0, "Portable fallback diverged from oracle"


def test_target_compile_guards(tmp_path: Path) -> None:
    """Verify compile-time guards allow ps_hash.c to build cleanly on all target arches."""
    import subprocess

    from pyscriptc import zigshim

    zig = shutil.which("zig") or zigshim.resolve_zig()
    if zig is None:
        pytest.skip("zig not installed")

    runtime_dir = Path(__file__).resolve().parent.parent / "src" / "pyscriptc" / "runtime"
    hash_c = runtime_dir / "ps_hash.c"
    assert hash_c.is_file()

    targets = [
        "wasm32-wasi",
        "x86_64-linux",
        "aarch64-linux",
        "x86_64-windows",
    ]
    for target in targets:
        cmd = [
            zig,
            "cc",
            "-target",
            target,
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{runtime_dir}",
            "-c",
            str(hash_c),
            "-o",
            str(tmp_path / f"ps_hash-{target}.o"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp_path)
        assert proc.returncode == 0, f"Compile for target {target} failed:\n{proc.stderr}"
