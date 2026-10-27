"""Deepest-stage matrix for self compilation: compile each `src/pyscriptc` module and record how far it gets.

Every module is compiled single file (`pyscriptc compile <file>`), first `--emit ir`, then `--emit llvm`,
then `--emit bin --call main` when the file defines `def main`. A PS-coded exit is a fence; anything else
nonzero is an error. Modules only progress while the previous stage succeeded.

Run: python -m benchmarks.compat2.selfhost_matrix [--src DIR] [--json OUT] [--timeout S]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SRC = ROOT / "src" / "pyscriptc"
DEFAULT_JSON = ROOT / "data" / "benchmarks" / "selfhost-matrix.json"
PS_CODE = re.compile(r"PS\d+")
STAGES = ("ir", "llvm", "bin")
MAIN_DEF = re.compile(r"^def main\s*\(", re.MULTILINE)


def _attempt(source: Path, emit: str, timeout: float) -> tuple[str, str]:
    """Run one compile stage. Returns ("ok", "") or (outcome, first PS code / detail)."""

    args = ["pyscriptc", "compile", str(source), "--emit", emit, "-o", str(source.parent / f".emit-{emit}")]
    if emit == "bin":
        args += ["--call", "main"]
    with tempfile.TemporaryDirectory(prefix="selfhost-") as tmp:
        args[args.index("-o") + 1] = str(Path(tmp) / f"out.{emit}")
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=tmp, env=None)
        except subprocess.TimeoutExpired:
            return "timeout", ""
        except OSError as exc:
            return "error", str(exc)
    if proc.returncode == 0:
        return "ok", ""
    text = proc.stderr or proc.stdout
    if proc.returncode == 3:
        line = next((ln.strip() for ln in text.splitlines() if PS_CODE.search(ln)), "")
        match = PS_CODE.search(line)
        return "fence", f"{match.group(0) if match else '?'}: {line[:160]}"
    tail = text.strip().splitlines()
    return "error", tail[-1].strip()[:160] if tail else f"exit {proc.returncode}"


def matrix(src: Path, timeout: float) -> dict[str, Any]:
    src = src.resolve()
    files = sorted(p for p in src.rglob("*.py") if p.name != "__main__.py" and "__pycache__" not in p.parts)
    rows: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(src)
        label = (
            rel.stem if rel.name != "__init__.py" else (src.name if rel.parent == Path(".") else rel.parent.as_posix())
        )
        row: dict[str, Any] = {"file": rel.as_posix(), "label": label}
        reached = "none"
        for stage in STAGES:
            if stage == "bin" and not MAIN_DEF.search(path.read_text(encoding="utf-8")):
                row["bin"] = "n/a"
                continue
            outcome, detail = _attempt(path, stage, timeout)
            row[stage] = outcome
            if outcome != "ok":
                row["fence" if outcome == "fence" else "stop"] = detail
                break
            reached = stage
        row["reached"] = reached
        rows.append(row)
    tally: dict[str, int] = {"ir": 0, "llvm": 0, "bin": 0, "fenced": 0, "errored": 0}
    for row in rows:
        if row["reached"] != "none":
            tally[row["reached"]] += 1
        elif row.get("fence"):
            tally["fenced"] += 1
        else:
            tally["errored"] += 1
    try:
        label = src.relative_to(ROOT).as_posix()
    except ValueError:
        label = str(src)
    return {"mode": "deepest-stage", "src": label, "modules": len(rows), "tally": tally, "rows": rows}


def render(data: dict[str, Any]) -> str:
    lines = [f"self-host emit matrix over {data['src']} ({data['modules']} modules)", ""]
    lines.append("| module | reached | stop |")
    lines.append("| --- | --- | --- |")
    for row in data["rows"]:
        stop = row.get("fence") or row.get("stop") or ""
        lines.append(f"| {row['label']} | {row['reached']} | {stop} |")
    lines.append("")
    lines.append(f"tally: {data['tally']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selfhost_matrix")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--json", type=Path, dest="json_out", default=None)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)

    started = time.monotonic()
    data = matrix(args.src, args.timeout)
    data["wall_s"] = round(time.monotonic() - started, 3)
    print(render(data))
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
