"""Generate data/compat/support.json from the compiler and probe data.

Status comes from compile probes (`pyscriptc.lower.lower_file`, parse-only, no
LLVM) listed in `scripts/support_data.toml`, from the closed import table in
`pyscriptc.stdlib_queue`, and from the live fence census of the pinned
top-100 sources. Only the "not supported" labels are hand-written.
Nightly measurements update JSON data files, not in-tree markdown.

Run: python scripts/gen_support_table.py [--check] [--out PATH] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.compat2.census import DEFAULT_SRC  # noqa: E402

from pyscriptc.coverage import build_report  # noqa: E402
from pyscriptc.diagnostics import DIAGNOSTICS  # noqa: E402
from pyscriptc.imports import _ERASED as ERASED  # noqa: E402
from pyscriptc.lower import lower_file  # noqa: E402
from pyscriptc.stdlib_queue import LOWERED, QUEUE  # noqa: E402

DATA = ROOT / "scripts" / "support_data.toml"
DEFAULT_DATA = ROOT / "data" / "compat" / "support.json"
SUPPORTED = ROOT / ".agents" / "docs" / "support" / "supported.md"
TOP100 = ROOT / ".agents" / "docs" / "support" / "compat-top100.md"
GREEN, YELLOW, RED = "🟢", "🟡", "🔴"

PRELUDE = """\
class P:
    a: int

    def __init__(self, a: int) -> None:
        self.a = a
"""
SIGNATURE = (
    "def f(s: str, b: bytes, n: int, x: float, t: bool, xs: list[int], ss: list[str],"
    " d: dict[str, int], st: set[int], p: P) -> None:"
)


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError, FileNotFoundError:
        return ""


def probe(imports: list[str], snippet: str) -> list[tuple[str, str]]:
    """Fences (code, message) from lowering the snippet inside a typed function."""
    pre, _, body = snippet.partition("\n@@\n") if "\n@@\n" in snippet else ("", "", snippet)
    lines = [f"import {m}" for m in imports] + [pre, PRELUDE, SIGNATURE]
    lines += ["    " + line for line in body.rstrip().split("\n")]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.py"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        module, _ = lower_file(str(path))
    return sorted({(fence["code"], fence["message"]) for fence in module["fences"]})


def status_of(ok: bool, no: bool) -> str:
    if ok and no:
        return YELLOW
    return GREEN if ok else RED


def evaluate(entry: dict[str, Any], kind: str, errors: list[str]) -> dict[str, Any]:
    names: list[str] = entry["names"]
    label = entry.get("label", ", ".join(f"`{n}`" for n in names))
    imports = (names if kind == "stdlib" else []) + entry.get("imports", [])
    if kind == "stdlib":
        pack = entry.get("pack", False)
        for name in names:
            inside = name in LOWERED
            erased = name in ERASED
            if pack:
                if inside:
                    errors.append(f"{name}: pack entry, but the import table lists it as lowered")
                continue
            if erased and entry.get("red", False):
                errors.append(f"{name}: annotation-only import, cannot be red")
            if not erased and entry.get("red", False) == inside:
                errors.append(f"{name}: import table says lowered={inside}, data says red={entry.get('red', False)}")
    codes: set[str] = set()
    if entry.get("red"):
        if kind == "stdlib":
            for name in names:
                found = {c for c, _ in probe([name], "pass")}
                if not found:
                    errors.append(f"{name}: expected an import fence")
                codes |= found
        for snippet, code in entry.get("probes", []):
            found = {c for c, _ in probe(imports, snippet)}
            if code not in found:
                errors.append(f"{label}: red probe expected {code}, got {sorted(found)}: {snippet!r}")
            codes.add(code)
        return {"label": label, "status": RED, "count": len(names), "missing": entry.get("note", "all"), "codes": codes}
    ok_snippets: list[str] = entry.get("ok", [])
    for snippet in ok_snippets:
        fences = probe(imports, snippet)
        if fences:
            errors.append(f"{label}: ok probe fences {fences[0]}: {snippet!r}")
    labels: list[str] = []
    for item in entry.get("no", []):
        text, snippet, code = item
        if code not in DIAGNOSTICS:
            errors.append(f"{label}: unknown code {code}")
        found = {c for c, _ in probe(imports, snippet)}
        if code not in found:
            errors.append(f"{label}: no-probe {text!r} expected {code}, got {sorted(found)}: {snippet!r}")
        codes.add(code)
        labels.append(text)
    status = status_of(bool(ok_snippets), bool(labels))
    if status == RED:
        missing = entry.get("note", "all")
    elif "works" in entry:
        missing = "only " + entry["works"]
    else:
        missing = ", ".join(labels)
    return {"label": label, "status": status, "count": len(names), "missing": missing, "codes": codes}


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = {GREEN: 0, YELLOW: 0, RED: 0}
    for r in rows:
        total[r["status"]] += r["count"]
    return total


def summary(label: str, rows: list[dict[str, Any]]) -> str:
    c = counts(rows)
    return f"{label}: {c[GREEN]} {GREEN} · {c[YELLOW]} {YELLOW} · {c[RED]} {RED}"


def library_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    pins = json.loads((ROOT / "benchmarks" / "compat" / "top100" / "pins.json").read_text())["packages"]
    versions = {p["import_name"]: (name, p["version"]) for name, p in pins.items()}
    notes: dict[str, str] = data.get("library_notes", {})
    rows = []
    for tree in sorted(DEFAULT_SRC.iterdir()):
        if not tree.is_dir():
            continue
        report = build_report(tree)
        summ = report["summary"]
        roots = Counter(f["family"] for f in report["fences"] if not f["derived"])
        codes = Counter(f["code"] for f in report["fences"])
        defs, fenced = summ["definitions"], summ["fencedDefinitions"]
        if summ["fences"] == 0:
            status = GREEN
        elif fenced * 2 <= defs:
            status = YELLOW
        else:
            status = RED
        name, version = versions.get(tree.name, (tree.name, "?"))
        blocking = ", ".join(f"{fam} ({n})" for fam, n in roots.most_common(3))
        rows.append(
            {
                "name": name,
                "version": version,
                "status": status,
                "ratio": f"{fenced}/{defs}",
                "blocking": notes.get(tree.name, blocking),
                "codes": ", ".join(c for c, _ in codes.most_common(3)),
            }
        )
    return rows


def build(date: str | None = None, pin: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Build data/compat/support.json.

    `pin` is an existing JSON document whose `generated` date and `commit`
    are reused, so a `--check` run over an unchanged tree sees the same bytes.
    """
    data = tomllib.loads(DATA.read_text(encoding="utf-8"))
    errors: list[str] = []
    builtins = [evaluate(e, "builtin", errors) for e in data["builtin"]]
    stdlib = [evaluate(e, "stdlib", errors) for e in data["stdlib"]]
    listed = {n for e in data["stdlib"] for n in e["names"]}
    for module in QUEUE:
        if module not in listed:
            errors.append(f"{module}: in the import table but missing from support_data.toml")

    packages = library_rows(data)

    # Convert sets to sorted lists for JSON serialization
    serialized_builtins = []
    for r in builtins:
        entry = dict(r)
        entry["codes"] = sorted(entry["codes"])
        serialized_builtins.append(entry)

    serialized_stdlib = []
    for r in stdlib:
        entry = dict(r)
        entry["codes"] = sorted(entry["codes"])
        serialized_stdlib.append(entry)

    c_builtins = counts(builtins)
    c_stdlib = counts(stdlib)
    c_packages = Counter(r["status"] for r in packages)

    measured_date = date or os.environ.get("COMPAT_DATE") or datetime.date.today().isoformat()
    pinned: dict[str, Any] | None = None
    if pin and pin.exists():
        try:
            pinned = json.loads(pin.read_text(encoding="utf-8"))
        except Exception:
            pinned = None

    doc = {
        "schema": "1",
        "generated": (pinned.get("generated") if pinned else None) or measured_date,
        "commit": (pinned.get("commit") if pinned else None) or _git("rev-parse", "HEAD"),
        "sources": {
            "support_data": "scripts/support_data.toml",
            "pins": "benchmarks/compat/top100/pins.json",
            "coverage": "pyscriptc coverage over benchmarks/compat/top100/src",
        },
        "summary": {
            "builtins": {
                "green": c_builtins[GREEN],
                "yellow": c_builtins[YELLOW],
                "red": c_builtins[RED],
            },
            "stdlib": {
                "green": c_stdlib[GREEN],
                "yellow": c_stdlib[YELLOW],
                "red": c_stdlib[RED],
            },
            "packages": {
                "green": c_packages[GREEN],
                "yellow": c_packages[YELLOW],
                "red": c_packages[RED],
            },
        },
        "builtins": serialized_builtins,
        "stdlib": serialized_stdlib,
        "packages": packages,
    }
    content = json.dumps(doc, indent=2) + "\n"
    return {str(DEFAULT_DATA): content}, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit with non-zero status if generated content differs")
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA, help="output path for support.json")
    parser.add_argument("--date", type=str, default=None, help="override the measured date (YYYY-MM-DD)")
    args = parser.parse_args()

    files, errors = build(date=args.date, pin=args.out if args.check else None)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    content = files[str(DEFAULT_DATA)]
    if args.check:
        target = args.out
        if not target.exists():
            print(f"{target} does not exist", file=sys.stderr)
            return 1
        existing = target.read_text(encoding="utf-8")
        if existing != content:
            print(f"{target} is out of date. Run: uv run python scripts/gen_support_table.py", file=sys.stderr)
            return 1
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
