"""Pydantic support baseline: 40 snippets, compile + run + output equality vs CPython.

Not part of the frozen compat2 gates: a standalone baseline for the pydantic
seam work (`.agents/notes/2026-11-02-pydantic-support.md`). Each snippet runs
twice: under CPython (the oracle) and under the compiler, on two planes:

- `island`: the whole module island-routed (`--fallback=cpython
  --embed-python=host`), pydantic bound through the per-import policy.
- `native`: the default lane, no fallback flags, no policy config — what a
  user gets out of the box.

A snippet scores `pass` (identical stdout, exit 0), `fence` (PS diagnostic,
exit != 0), `wrong-output` (ran, stdout differs), `crash` (non-zero exit
without a PS diagnostic), or `timeout`. `BASELINE.json` records the latest
run; `run.py --update` rewrites it.

Run: `uv run python benchmarks/pydantic/run.py [--update] [--plane island]`
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CORPUS = Path(__file__).resolve().parent / "corpus"
PSC = REPO / ".venv" / "bin" / "pyscriptc"

# The oracle interpreter must have pydantic importable: the compiler's own
# venv usually does not, so an env var (or a `pydantic`-bearing interpreter)
# is probed in order.
ORACLE_CANDIDATES = [sys.executable] + [
    str(p) for p in (REPO.parent / "pydenv" / "bin" / "python", Path("/tmp/pydenv/bin/python")) if p.exists()
]

TIMEOUT = 300
PLANES = ("native", "island")
OUTCOMES = ("pass", "fence", "wrong-output", "crash", "timeout")


@dataclass
class Case:
    name: str
    plane: str
    outcome: str
    code: str = ""  # first PS code, for fences
    detail: str = ""  # first diverging line, for wrong-output


def oracle() -> str:
    """The first interpreter that imports pydantic cleanly."""
    for exe in ORACLE_CANDIDATES:
        probe = subprocess.run([exe, "-c", "import pydantic"], capture_output=True)
        if probe.returncode == 0:
            return exe
    print("no oracle interpreter with pydantic found", file=sys.stderr)
    raise SystemExit(1)


def cpython_expect(path: Path, exe: str) -> tuple[str, int]:
    proc = subprocess.run([exe, str(path)], capture_output=True, text=True, timeout=TIMEOUT, cwd=path.parent)
    return proc.stdout, proc.returncode


def run_plane(path: Path, plane: str, tmp: Path) -> tuple[str, int, str]:
    """Run one snippet on one plane; returns (stdout, exit code, first PS code)."""
    if plane == "island":
        cmd = [str(PSC), "run", "--fallback=cpython", "--embed-python=host", str(path)]
    else:
        cmd = [str(PSC), "run", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, cwd=path.parent)
    except subprocess.TimeoutExpired:
        return "", 124, ""
    stdout, err = proc.stdout, proc.stderr
    code = ""
    match = re.search(r"\b(PS\d{4})\b", err)
    if match:
        code = match.group(1)
    return stdout, proc.returncode, code


def score(expect: tuple[str, int], got: tuple[str, int]) -> Case:
    e_out, e_code = expect
    g_out, g_code, ps = got
    if g_code == 124:
        return Case(name="", plane="", outcome="timeout")
    if ps.startswith("PS"):
        return Case(name="", plane="", outcome="fence", code=ps)
    if g_code != e_code:
        return Case(name="", plane="", outcome="crash", detail=f"exit {g_code} vs {e_code}")
    if g_out != e_out:
        first = next(
            (line for line, eline in zip(g_out.splitlines(), e_out.splitlines(), strict=False) if line != eline), ""
        )
        return Case(name="", plane="", outcome="wrong-output", detail=first[:120])
    return Case(name="", plane="", outcome="pass")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="rewrite BASELINE.json")
    ap.add_argument("--plane", choices=(*PLANES, "both"), default="both")
    args = ap.parse_args()
    planes = PLANES if args.plane == "both" else (args.plane,)
    snippets = sorted(p for p in CORPUS.glob("*.py") if not p.name.startswith("_"))
    if not snippets:
        print(f"no snippets in {CORPUS}", file=sys.stderr)
        raise SystemExit(1)
    results: list[Case] = []
    exe = oracle()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for path in snippets:
            expect = cpython_expect(path, exe)
            if expect[1] != 0:
                print(f"skip {path.name}: oracle itself fails", file=sys.stderr)
                continue
            for plane in planes:
                case = score(expect, run_plane(path, plane, tmp))
                case.name, case.plane = path.stem, plane
                results.append(case)
    tally: dict[str, dict[str, int]] = {plane: dict.fromkeys(OUTCOMES, 0) for plane in planes}
    for case in results:
        tally[case.plane][case.outcome] += 1
    for plane in planes:
        total = sum(tally[plane].values())
        passed = tally[plane]["pass"]
        print(f"{plane}: {passed}/{total} pass ({100 * passed / total:.1f}%)")
    for case in results:
        if case.outcome != "pass":
            tag = f" [{case.code or case.detail}]"
            print(f"  {case.plane:7s} {case.name}: {case.outcome}{tag}")
    if args.update:
        baseline = Path(__file__).resolve().parent / "BASELINE.json"
        payload = {
            "snippets": len(snippets),
            "planes": {plane: {"pass": tally[plane]["pass"], "total": sum(tally[plane].values())} for plane in planes},
            "cases": [
                {"name": c.name, "plane": c.plane, "outcome": c.outcome, "code": c.code, "detail": c.detail}
                for c in results
            ],
        }
        baseline.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {baseline}")


if __name__ == "__main__":
    main()
