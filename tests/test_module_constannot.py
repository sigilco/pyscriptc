"""Differential tests for unannotated foldable module constants, module statements,
and cheap stdlib import rows (platform, calendar, zipfile, tarfile) (#502).
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from tests.test_gates import run_cli, write_case


def differential(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    """Compile, run, and pin the three observable channels to CPython."""
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / (name + ".bin"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "-o", out)
    expected = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (got.returncode, got.stdout, got.stderr) == (expected.returncode, expected.stdout, expected.stderr)
    return got


def test_foldable_unannotated_primitives(tmp_path: Path) -> None:
    source = """
TIMEOUT = 30 * 60
BASE = 100
OFFSET = -10
COUNT = BASE + OFFSET
FACTOR = 2.5
SCALED = FACTOR * 3.0
HALF = FACTOR / 2.0
FLAG = True
INVERTED = not FLAG
MASK = ~0xFF
LABEL = "pyscriptc-" + "v1"

print(TIMEOUT, COUNT, SCALED, HALF, FLAG, INVERTED, MASK, LABEL)
"""
    differential(tmp_path, "prim.py", source)


def test_foldable_unannotated_arithmetic_operations(tmp_path: Path) -> None:
    source = """
A = 15
B = 4
ADD = A + B
SUB = A - B
MUL = A * B
DIV = A / B
FLOOR = A // B
MOD = A % B
POW = B ** 2
SHL = 1 << 4
SHR = 32 >> 2
BAND = A & 6
BOR = A | 2
BXOR = A ^ 7

print(ADD, SUB, MUL, DIV, FLOOR, MOD, POW, SHL, SHR, BAND, BOR, BXOR)
"""
    differential(tmp_path, "arith.py", source)


def test_foldable_unannotated_tuples(tmp_path: Path) -> None:
    source = """
X = 10
Y = 20
COORDS = (X, Y, X + Y, -5, "origin", True)
FIRST = COORDS[0]
LAST = COORDS[1]

print(COORDS[0], COORDS[1], COORDS[2], COORDS[3], COORDS[4], COORDS[5], FIRST, LAST)
"""
    differential(tmp_path, "tup.py", source)


def test_multi_and_unpack_assignment(tmp_path: Path) -> None:
    source = """
A = B = 42
X, Y = 10, 20 + 5

print(A, B, X, Y)
"""
    differential(tmp_path, "multi_assign.py", source)


def test_module_for_reassigning_variable(tmp_path: Path) -> None:
    source = """
total: int = 0
for i in (1, 2, 3, 4, 5):
    total = total + i

print(total)
"""
    differential(tmp_path, "for_reassign.py", source)


def test_module_argparse_slot(tmp_path: Path) -> None:
    source = """
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--name")
args = parser.parse_args()
print("argparse-ok")
"""
    differential(tmp_path, "mod_argparse.py", source)


def test_platform_module_differential(tmp_path: Path) -> None:
    source = """
import platform

s: str = platform.system()
m: str = platform.machine()
v: str = platform.python_version()
print(s == platform.system(), len(m) > 0, len(v) > 0)
"""
    differential(tmp_path, "plat_diff.py", source)


def test_calendar_module_differential(tmp_path: Path) -> None:
    source = """
import calendar

l24: bool = calendar.isleap(2024)
l23: bool = calendar.isleap(2023)
ld: int = calendar.leapdays(2000, 2025)
wd: int = calendar.weekday(2024, 1, 1)
m: int = calendar.MONDAY
s: int = calendar.SUNDAY
print(l24, l23, ld, wd, m, s)
"""
    differential(tmp_path, "cal_diff.py", source)


def test_zipfile_and_tarfile_differential(tmp_path: Path) -> None:
    zf_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(zf_path, "w") as zf:
        zf.writestr("test.txt", "content")

    tf_path = tmp_path / "sample.tar"
    with tarfile.open(tf_path, "w") as tf:
        tf.add(zf_path, arcname="sample.zip")

    plain_path = tmp_path / "plain.txt"
    plain_path.write_text("not an archive")

    source = f"""
import zipfile
import tarfile

z_real: bool = zipfile.is_zipfile({repr(str(zf_path))})
z_fake: bool = zipfile.is_zipfile({repr(str(plain_path))})
t_real: bool = tarfile.is_tarfile({repr(str(tf_path))})
t_fake: bool = tarfile.is_tarfile({repr(str(plain_path))})
zs: int = zipfile.ZIP_STORED
zd: int = zipfile.ZIP_DEFLATED
print(z_real, z_fake, t_real, t_fake, zs, zd)
"""
    differential(tmp_path, "archives_diff.py", source)


def test_unlowered_stdlib_fences_cleanly(tmp_path: Path) -> None:
    cases = [
        ("plat_fence.py", "import platform\ndef f() -> None:\n    platform.processor()\n"),
        ("cal_fence.py", "import calendar\ndef f() -> None:\n    calendar.monthcalendar(2024, 1)\n"),
        ("zip_fence.py", "import zipfile\ndef f() -> None:\n    zipfile.ZipFile('x')\n"),
        ("tar_fence.py", "import tarfile\ndef f() -> None:\n    tarfile.TarFile('x')\n"),
    ]
    for name, source in cases:
        p = write_case(tmp_path, name, source)
        got = run_cli("compile", str(p), "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
        assert got.returncode == 3
        assert "is not in the lowered subset of" in got.stderr
