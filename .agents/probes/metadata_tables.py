"""Probe the compile-time class metadata tables against the CPython oracle.

The metadata surface (roadmap item 3 of the runtime-reflection note) has no
runtime representation: `dataclasses.fields`, `asdict`, `astuple`, `replace`,
`vars(o)` and the enum member table all read a compile-time table the
lowered class record already owns. This probe measures that surface end to
end: each case compiles to a native binary and its stdout is diffed against
the CPython oracle, so a case that fences and a case that lowers to the
wrong answer are different verdicts.

Usage:
    python .agents/probes/metadata_tables.py <checkout-dir> <label>

Prints one line per case: <label>\t<case>\t<verdict>\t<detail>, where the
verdict is MATCH (compiles and matches the oracle), FENCE (does not compile)
or DIFF (compiles, differs from the oracle).

Set PYSCRIPT_PY to the interpreter that has pyscriptc installed (the
worktree venv); the checkout directory supplies the source under test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PYBIN = os.environ.get("PYSCRIPT_PY", sys.executable)

CASES: dict[str, str] = {
    "fields": """
from dataclasses import dataclass, fields

@dataclass
class Point:
    x: int
    y: int = 0

def run() -> int:
    for f in fields(Point):
        print(f.name)
    return 0
""",
    "asdict": """
from dataclasses import dataclass, asdict

@dataclass
class Point:
    x: int
    y: int

def run() -> int:
    d = asdict(Point(1, 2))
    print(d["x"], d["y"])
    return 0
""",
    "astuple": """
from dataclasses import dataclass, astuple

@dataclass
class Point:
    x: int
    y: int

def run() -> int:
    t = astuple(Point(1, 2))
    print(t[0], t[1])
    return 0
""",
    "replace": """
from dataclasses import dataclass, replace

@dataclass
class Point:
    x: int
    y: int = 0
    tag: str = "t"

def run() -> int:
    p = Point(3, 4, tag="z")
    q = replace(p, y=9)
    print(q.x, q.y, q.tag)
    return 0
""",
    "vars": """
from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

def run() -> int:
    v = vars(Point(1, 2))
    print(v["x"], v["y"])
    return 0
""",
    "enum_lookup_name": """
from enum import IntEnum

class Color(IntEnum):
    RED = 1
    GREEN = 2

def run() -> int:
    print(Color["GREEN"])
    return 0
""",
    "enum_lookup_value": """
from enum import IntEnum

class Color(IntEnum):
    RED = 1
    GREEN = 2

def run() -> int:
    print(Color(1))
    return 0
""",
    "enum_members": """
from enum import IntEnum

class Color(IntEnum):
    RED = 1
    GREEN = 2

def run() -> int:
    print(Color.__members__["RED"])
    return 0
""",
    "enum_list": """
from enum import IntEnum

class Color(IntEnum):
    RED = 1
    GREEN = 2

def run() -> int:
    for v in list(Color):
        print(v)
    return 0
""",
    "field_flags": """
from dataclasses import dataclass, field

@dataclass
class Point:
    x: int
    y: int = field(default=0, kw_only=True)
    tag: str = field(default="t", init=False, repr=False)

def run() -> int:
    print(Point(1, y=2).y)
    return 0
""",
}


def oracle(path: Path) -> str:
    source = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "value = m.run()\n"
        "print('%.17g' % value if isinstance(value, float) else value)\n"
    )
    proc = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    return proc.stdout if proc.returncode == 0 else f"ORACLE-ERR: {proc.stderr.strip().splitlines()[-1:]}"


def compiled(name: str, src: str, checkout: str, tmp: Path) -> tuple[int, str]:
    path = tmp / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    out = tmp / f"{name}.bin"
    env = {**os.environ, "PYTHONPATH": str(Path(checkout) / "src")}
    proc = subprocess.run(
        [
            PYBIN,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--call",
            "run",
            "--args",
            "[]",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        first = proc.stderr.strip().splitlines()
        return 1, first[0] if first else "compile failed"
    return 0, proc.stdout


def main() -> int:
    checkout, label = sys.argv[1], sys.argv[2]
    with tempfile.TemporaryDirectory(dir="/home/own-agents") as tmpdir:
        tmp = Path(tmpdir)
        for name, src in CASES.items():
            path = tmp / f"{name}.py"
            path.write_text(src, encoding="utf-8")
            want = oracle(path)
            rc, got = compiled(name, src, checkout, tmp)
            if rc != 0:
                verdict = "FENCE"
            elif got == want:
                verdict = "MATCH"
            else:
                verdict = "DIFF"
            detail = got.strip().replace("\n", " | ") if rc == 0 else got[:110]
            print(f"{label}\t{name}\t{verdict}\t{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
