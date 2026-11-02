"""The float spelling: shortest round-trip digits, CPython's notation choice.

`ps_f64_str` takes the shortest round-trip digits from Ryu (ulfjack/ryu
v2.0, vendored under `runtime/vendor/ryu`), not from `%.*g` on the host
libc, so the bytes do not depend on which libc links the program. The
notation choice stays CPython's: the fixed form while `-4 <= exp10 <= 15`,
the `d.ddde+NN` form outside it, always a `.` (`inf`, `nan` and `-0.0`
keep their own spellings).

The property tests below check the spelling against CPython 3.14's
`repr(float)` over a fixed edge-case set and 10,000 random bit patterns,
and the probe builds the same runtime unit against glibc and against musl
(zig cc) to prove the two libcs agree byte for byte.
"""

from __future__ import annotations

import math
import random
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import ROOT, run_cli, write_case

RUNTIME = ROOT / "src" / "pyscriptc" / "runtime"

# The edge cases the spike named, plus the boundaries of CPython's notation
# choice and the subnormal range.
EDGE_VALUES: tuple[float, ...] = (
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.5,
    2.5,
    -2.5,
    16.1,
    0.1,
    1.0 / 3.0,
    0.1 + 0.2,
    10.0,
    100.0,
    150.0,
    1000.0,
    1e15,
    1e16,
    1e17,
    1e22,
    1e-4,
    1e-5,
    1234567890123456.0,
    9.999999999999999e20,
    1.23456789e-5,
    1.0000000000000002e16,
    5e-324,
    -5e-324,
    2.2250738585072014e-308,
    1.7976931348623157e308,
    -1.7976931348623157e308,
    2.0**53,
    2.0**-1074,
)

# The probe: reads hex bit patterns on stdin, prints the spelling of each.
PROBE = """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "ps_rt.h"

int main(void) {
    char line[64];
    PsBuf out;
    while (fgets(line, sizeof line, stdin)) {
        uint64_t bits = strtoull(line, NULL, 16);
        double v;
        memcpy(&v, &bits, sizeof v);
        ps_f64_str(v, &out);
        printf("%s\\n", out.data);
    }
    return 0;
}
"""


def _random_values(count: int, seed: int) -> list[float]:
    """`count` random doubles, skipping the NaN and infinity patterns."""
    rng = random.Random(seed)
    values: list[float] = []
    while len(values) < count:
        bits = rng.getrandbits(64)
        value = struct.unpack("<d", struct.pack("<Q", bits))[0]
        if math.isnan(value) or math.isinf(value):
            continue
        values.append(value)
    return values


def _hex_bits(values: list[float]) -> str:
    return "".join(f"{struct.unpack('<Q', struct.pack('<d', v))[0]:016x}\n" for v in values)


def _runtime_sources() -> list[str]:
    """The runtime units a probe links: every `.c` but the island one."""
    return [str(p) for p in sorted(RUNTIME.glob("*.c")) if p.name != "ps_island.c"]


def _build_probe(tmp_path: Path, name: str, cc: list[str], extra: list[str]) -> Path:
    """Build the probe with `cc`; `extra` rides the compile line."""
    source = tmp_path / f"probe_{name}.c"
    source.write_text(PROBE, encoding="utf-8")
    binary = tmp_path / f"probe_{name}"
    built = subprocess.run(
        [
            *cc,
            "-O2",
            f"-I{RUNTIME}",
            f"-I{RUNTIME / 'vendor'}",
            *extra,
            str(source),
            *_runtime_sources(),
            "-lm",
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert built.returncode == 0, f"{name} probe build failed: {built.stderr}"
    return binary


def _zig() -> list[str] | None:
    """The zig command for a musl build, or None when zig is unreachable."""
    from pyscriptc import zigshim

    zigshim.ensure_on_path()
    zig = shutil.which("zig")
    return [zig] if zig else None


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="the oracle is CPython 3.14's repr")
def test_float_spelling_matches_cpython_repr(tmp_path: Path) -> None:
    """The runtime spelling is CPython 3.14's `repr(float)` for 10,000 random
    doubles plus the edge cases."""
    cc = shutil.which("clang") or shutil.which("cc")
    assert cc is not None, "no C compiler on PATH"
    binary = _build_probe(tmp_path, "glibc", [cc], [])
    values = list(EDGE_VALUES) + _random_values(10_000, seed=20261102)
    ran = subprocess.run(
        [str(binary)], input=_hex_bits(values), capture_output=True, text=True, timeout=300
    )
    assert ran.returncode == 0, ran.stderr
    got = ran.stdout.splitlines()
    assert len(got) == len(values)
    mismatches = [
        (value, spelling) for value, spelling in zip(values, got, strict=True) if repr(value) != spelling
    ]
    assert not mismatches, f"{len(mismatches)} spellings differ from CPython: {mismatches[:5]}"


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="the oracle is CPython 3.14's repr")
def test_float_spelling_is_the_same_on_glibc_and_musl(tmp_path: Path) -> None:
    """The spelling does not depend on the libc: glibc and musl agree byte
    for byte, and both agree with CPython 3.14's repr."""
    cc = shutil.which("clang") or shutil.which("cc")
    assert cc is not None, "no C compiler on PATH"
    zig = _zig()
    if zig is None:
        pytest.skip("zig is not reachable, so the musl probe cannot be built")
    glibc = _build_probe(tmp_path, "glibc", [cc], [])
    musl = _build_probe(tmp_path, "musl", [*zig, "cc"], ["-target", "x86_64-linux-musl"])
    values = list(EDGE_VALUES) + _random_values(10_000, seed=20261102)
    payload = _hex_bits(values)
    outputs = {}
    for name, binary in (("glibc", glibc), ("musl", musl)):
        ran = subprocess.run([str(binary)], input=payload, capture_output=True, text=True, timeout=300)
        assert ran.returncode == 0, ran.stderr
        outputs[name] = ran.stdout.splitlines()
    assert outputs["glibc"] == outputs["musl"], "the two libcs spell a float differently"
    mismatches = [(v, s) for v, s in zip(values, outputs["glibc"], strict=True) if repr(v) != s]
    assert not mismatches, f"{len(mismatches)} spellings differ from CPython: {mismatches[:5]}"


def test_compiled_float_print_matches_cpython(tmp_path: Path) -> None:
    """A compiled program's float prints are CPython's bytes."""
    path = write_case(
        tmp_path,
        "floats.py",
        "\n".join(
            [
                "print(0.1 + 0.2)",
                "print(1.5, 2.0, -0.25)",
                "print(1e16)",
                "print(1e-5)",
                "print(2.5e-300)",
                "print(10.0)",
                "print(100.0)",
                "print(0.0, -0.0)",
                "print(1/3)",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "floats"))
    assert got.returncode == 0, got.stderr
    oracle = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True)
    assert got.stdout == oracle.stdout


def test_whole_floats_print_in_fixed_notation(tmp_path: Path) -> None:
    """The exponent form CPython does not use at that magnitude is gone:
    `10.0` prints `10.0`, not `1e+01`. The entry left the divergence list:
    `ps_f64_str` takes the shortest digits from Ryu and applies CPython's own
    notation rule, so the exponent form cannot appear."""
    path = write_case(
        tmp_path,
        "whole.py",
        "\n".join(
            [
                "def show(v: float) -> str:",
                "    return str(v)",
                "",
                "",
                "print(show(10.0))",
                "print(show(100.0))",
                "print(show(1000000000000000.0))",
                "print(show(1e16))",
                "print(show(1e15))",
                "",
            ]
        ),
    )
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "main", "-o", str(tmp_path / "whole"))
    assert got.returncode == 0, got.stderr
    oracle = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True)
    assert got.stdout == oracle.stdout
    assert "1e+01" not in got.stdout
