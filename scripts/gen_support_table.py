"""Generate docs/supported.md and docs/compat-top100.md from the compiler.

Status comes from compile probes (`pyscriptc.lower.lower_file`, parse-only, no
LLVM) listed in `scripts/support_data.toml`, from the closed import table in
`pyscriptc.stdlib_queue`, and from the live fence census of the pinned
top-100 sources. Only the "not supported" labels are hand-written.

Run: python scripts/gen_support_table.py [--check]
"""

from __future__ import annotations

import argparse
import json
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
SUPPORTED = ROOT / "docs" / "supported.md"
TOP100 = ROOT / "docs" / "compat-top100.md"
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
        for name in names:
            inside = name in LOWERED
            erased = name in ERASED
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
    if "works" in entry and status != GREEN:
        missing = "only " + entry["works"]
    else:
        missing = ", ".join(labels)
    return {"label": label, "status": status, "count": len(names), "missing": missing, "codes": codes}


def table(rows: list[dict[str, Any]], first: str) -> str:
    order = {RED: 0, YELLOW: 1, GREEN: 2}
    rows = sorted(rows, key=lambda r: (order[r["status"]], r["label"]))
    out = [f"| {first} | status | not supported | fences |", "| --- | --- | --- | --- |"]
    for r in rows:
        codes = ", ".join(sorted(r["codes"])) if r["status"] != GREEN else ""
        missing = r["missing"] if r["status"] != GREEN else ""
        out.append(f"| {r['label']} | {r['status']} | {missing} | {codes} |")
    return "\n".join(out)


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


def render_top100(data: dict[str, Any]) -> str:
    rows = library_rows(data)
    c = Counter(r["status"] for r in rows)
    order = {RED: 0, YELLOW: 1, GREEN: 2}
    rows.sort(key=lambda r: (order[r["status"]], r["name"]))
    out = [
        "# Top-100 library compatibility",
        "",
        f"pinned pure-Python libraries: {c[GREEN]} {GREEN} · {c[YELLOW]} {YELLOW} · {c[RED]} {RED}",
        "",
        f"{GREEN} compiles with no fence · {YELLOW} at most half of the definitions fence · {RED} more than half"
        " fence. Counts are live `pyscriptc coverage` runs over `benchmarks/compat/top100/src` (pins and hashes in"
        " `pins.json`). A library only counts toward the score when one of its own functions runs and matches CPython."
        " Fence codes are per construct, see [supported.md](supported.md). Tiers T0 to T3:"
        " [refused-features.md](refused-features.md). Deliberate differences: [divergences.md](divergences.md).",
        "",
        "| library | version | status | fenced defs | top blocking features (fences) | top codes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        out.append(
            f"| `{r['name']}` | {r['version']} | {r['status']} | {r['ratio']} | {r['blocking']} | {r['codes']} |"
        )
    out += ["", "Regenerate: `uv run python scripts/gen_support_table.py`.", ""]
    return "\n".join(out)


def render_supported(data: dict[str, Any], errors: list[str]) -> str:
    builtins = [evaluate(e, "builtin", errors) for e in data["builtin"]]
    stdlib = [evaluate(e, "stdlib", errors) for e in data["stdlib"]]
    listed = {n for e in data["stdlib"] for n in e["names"]}
    for module in QUEUE:
        if module not in listed:
            errors.append(f"{module}: in the import table but missing from support_data.toml")
    head = data["text"]
    out = [
        "# What is supported",
        "",
        f"{summary('builtins', builtins)} | {summary('stdlib', stdlib)}",
        "",
        head["how_to_read"].strip(),
        "",
        "## Builtins",
        "",
        table(builtins, "builtin"),
        "",
        "## Standard library",
        "",
        table(stdlib, "module"),
        "",
        head["footer"].strip(),
        "",
    ]
    return "\n".join(out)


def build() -> tuple[dict[str, str], list[str]]:
    data = tomllib.loads(DATA.read_text(encoding="utf-8"))
    errors: list[str] = []
    files = {str(SUPPORTED): render_supported(data, errors), str(TOP100): render_top100(data)}
    return files, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files, errors = build()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    stale = [p for p, text in files.items() if not Path(p).exists() or Path(p).read_text(encoding="utf-8") != text]
    if args.check:
        if stale:
            print("stale (run scripts/gen_support_table.py): " + ", ".join(stale), file=sys.stderr)
        return 1 if stale else 0
    for path, text in files.items():
        Path(path).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
