"""Generate compat/data/what_will_fail.json from the R0 corpus fences.

Lists what fails and why (divergences and compiler fences, not what works),
grouped by root cause, using the public corpus fences only.
Nightly measurements update JSON data files, not in-tree markdown.

Run: python scripts/gen_what_will_fail.py [--check] [--out PATH] [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.real_scripts.measure import ROOT_CAUSE, _module_of, script_paths  # noqa: E402

from pyscriptc.coverage import build_report  # noqa: E402

SCRIPTS_DIR = ROOT / "benchmarks" / "real_scripts" / "scripts"
REPORT_PATH = ROOT / "benchmarks" / "real_scripts" / "REPORT.md"
DEFAULT_DATA = ROOT / "compat" / "data" / "what_will_fail.json"

SECTION_TITLES = {
    "stdlib-surface": (
        "Standard library surface",
        (
            "Calls to standard library modules in the closed import table whose "
            "specific function or method is not yet lowered to native IR."
        ),
    ),
    "imports": (
        "Closed import table",
        (
            "Standard library and external modules that are not yet in the closed "
            "import table. Importing them halts compilation with a fence."
        ),
    ),
    "call-table": (
        "Call signatures and keyword arguments",
        (
            "Calls that pass keyword arguments, unsupported argument counts, "
            "or non-scalar arguments halt at the call table."
        ),
    ),
    "inference": (
        "Type annotations and inference",
        (
            "pyscriptc requires type annotations on function parameters, return types, "
            "and local variable declarations that cannot be inferred from literals or lowered calls."
        ),
    ),
    "statements": (
        "Statements and language constructs",
        "Statement forms, unpack patterns, and language constructs that have not yet been lowered.",
    ),
    "classes": (
        "Classes and object model",
        "Class attributes, method declarations, and class decorators outside the lowered object model.",
    ),
    "operators": (
        "Operators",
        "Operators applied to unlowered operand type combinations halt with an operator fence.",
    ),
    "checker": (
        "Type checking",
        (
            "The type-checker preflight rejects type mismatches between values and "
            "declared annotations before lowering begins."
        ),
    ),
    "reflection": (
        "Dynamic reflection",
        (
            "Dynamic argument unpacking and keyword forwarding that cannot be lowered "
            "natively route to the island runtime."
        ),
    ),
}

ROOT_CAUSE_ORDER = [
    "stdlib-surface",
    "imports",
    "call-table",
    "inference",
    "statements",
    "classes",
    "operators",
    "checker",
    "reflection",
]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError, FileNotFoundError:
        return ""


def _read_corpus_date_and_count() -> tuple[str, int]:
    """Read measurement date and script count from REPORT.md."""
    date = "2026-10-24"
    count = 39
    if REPORT_PATH.exists():
        text = REPORT_PATH.read_text(encoding="utf-8")
        date_match = re.search(r"Generated\s+(\d{4}-\d{2}-\d{2})", text)
        if date_match:
            date = date_match.group(1)
        count_match = re.search(r"Rung R0:\s+\d+\s+of\s+(\d+)\s+vendored", text)
        if count_match:
            count = int(count_match.group(1))
    return date, count


def _classify_fence(fence: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Classify a fence into (root_cause, module, construct, code, message)."""
    msg = fence["message"]
    code = fence["code"]
    family = fence["family"]
    cause = ROOT_CAUSE.get(family, "other")
    if family == "reflection":
        cause = "reflection"

    # Module resolution
    mod = _module_of(msg)
    if not mod and family.startswith("stdlib:"):
        mod = family.split(":", 1)[1]

    msg_lower = msg.lower()
    if not mod or mod == "builtins":
        if "defaultdict" in msg_lower:
            mod = "collections"
        elif (
            "any is not a lowered type" in msg_lower
            or "any is not a value" in msg_lower
            or "callable[" in msg_lower
            or "dict is not lowered; use dict[" in msg_lower
        ):
            mod = "typing"
        elif "datetime" in msg_lower:
            mod = "datetime"
        elif "path." in msg_lower or "formatting path" in msg_lower:
            mod = "pathlib"
        elif "hashlib" in msg_lower:
            mod = "hashlib"
        elif "argparse" in msg_lower:
            mod = "argparse"
        elif "sys.exit" in msg_lower:
            mod = "sys"
        elif "posixpath" in msg_lower or "os.path" in msg_lower:
            mod = "os.path"
        elif "random.random" in msg_lower:
            mod = "random"
        elif "itertools" in msg_lower:
            mod = "itertools"
        elif "dataclasses" in msg_lower:
            mod = "dataclasses"
        elif "json." in msg_lower:
            mod = "json"
        elif "os." in msg_lower:
            mod = "os"
        elif "subprocess" in msg_lower:
            mod = "subprocess"
        elif any(
            b in msg_lower
            for b in (
                "print is lowered",
                "input expects",
                "abs is lowered",
                "open keyword",
                "zip takes",
                "zip() of",
            )
        ):
            mod = "builtins"
        elif family in (
            "param-inference",
            "return-inference",
            "local-inference",
            "name-resolution",
            "construct-other",
            "module-assign",
            "field-lookup",
            "parameter-forms",
            "operator",
            "type-mismatch",
            "reflection",
        ):
            mod = "builtins"

    if not mod:
        mod = "builtins"

    # Construct description
    construct = ""
    if family == "import-table":
        construct = f"import {mod}"
    elif family.startswith("stdlib:"):
        m = re.match(r"([a-zA-Z0-9_.]+) is not in the lowered subset", msg)
        construct = f"`{m.group(1)}`" if m else f"`{msg.split()[0]}`"
    elif family == "call-arity":
        if "hashlib" in msg:
            construct = "`hashlib.sha256()`"
        elif "sys.exit" in msg:
            construct = "`sys.exit()`"
        elif "input expects" in msg:
            construct = "`input()` with prompt"
        elif "open keyword" in msg:
            construct = "`open(newline=...)`"
        elif "print is lowered" in msg:
            construct = "`print(list)`"
        elif "abs is lowered" in msg:
            construct = "`abs()` on non-number"
        elif "zip takes" in msg:
            construct = "`zip()` with >2 args"
        elif "argparse" in msg:
            construct = "`argparse` option/default"
        else:
            construct = "call arity"
    elif family == "keyword-args":
        construct = "keyword arguments"
    elif family == "param-inference":
        construct = "parameter without annotation"
    elif family == "return-inference":
        construct = "function without return annotation"
    elif family == "local-inference":
        construct = "local variable without annotation"
    elif family == "name-resolution":
        construct = "name read after unannotated assignment"
    elif family == "unlowered-call":
        construct = "call to unlowered function"
    elif family == "construct-other":
        if "DateTime.strftime" in msg:
            construct = "`DateTime.strftime`"
        elif "Path.endswith" in msg:
            construct = "`Path.endswith`"
        elif "bool fields" in msg:
            construct = "dataclass bool field"
        elif "for unpacks" in msg:
            construct = "nested tuple unpack in loop"
        elif "formatting" in msg:
            construct = "f-string format object"
        elif "global" in msg:
            construct = "`global` statement"
        elif "zip()" in msg:
            construct = "`zip()` of nested list"
        elif "call of attribute" in msg:
            m = re.search(r"\(([^)]+)\)", msg)
            construct = f"`{m.group(1)}()`" if m else "method call on object"
        else:
            construct = msg.split(" is ")[0] if " is " in msg else msg
    elif family == "module-assign":
        construct = "non-constant module assignment"
    elif family == "field-lookup":
        construct = "class attribute access"
    elif family == "parameter-forms":
        construct = "`@dataclass` without arguments"
    elif family == "operator":
        construct = "operator on unlowered types"
    elif family == "type-mismatch":
        construct = "type annotation mismatch"
    elif family == "generic-alias":
        construct = "`collections.defaultdict`"
    elif family == "dynamic-annotation":
        construct = "`typing.Any`"
    elif family == "forward-reference":
        construct = "forward class reference"
    elif family == "unlowered-type-name":
        construct = "unlowered type annotation"
    elif family == "annotation-other":
        construct = "unlowered type annotation"
    elif family == "reflection":
        construct = "`**kwargs` forwarding"
    else:
        construct = family

    return cause, mod, construct, code, msg


def build(date: str | None = None, pin: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Build compat/data/what_will_fail.json from the R0 corpus fences.

    `pin` is an existing JSON document whose `generated` date and `commit`
    are reused, so a `--check` run over an unchanged tree sees the same bytes.
    """
    errors: list[str] = []
    default_date, script_count = _read_corpus_date_and_count()
    measured_date = date or default_date

    scripts = script_paths(SCRIPTS_DIR)
    if not scripts:
        errors.append(f"no scripts found in {SCRIPTS_DIR}")
        return {}, errors

    # Deduplicate fences per (cause, mod, construct, code)
    deduped: dict[tuple[str, str, str, str], str] = {}
    codes_seen: set[str] = set()

    for path in scripts:
        try:
            report = build_report(path)
        except Exception as exc:
            errors.append(f"{path.stem}: {type(exc).__name__}: {exc}")
            continue

        for fence in report.get("fences", []):
            if fence.get("derived", False):
                continue
            cause, mod, construct, code, msg = _classify_fence(fence)
            codes_seen.add(code)
            key = (cause, mod, construct, code)
            if key not in deduped:
                deduped[key] = msg

    # Group rows by root cause
    by_cause: dict[str, list[tuple[str, str, str, str]]] = {cause: [] for cause in ROOT_CAUSE_ORDER}
    for (cause, mod, construct, code), msg in deduped.items():
        if cause not in by_cause:
            by_cause[cause] = []
        by_cause[cause].append((mod, construct, code, msg))

    causes = []
    for cause in ROOT_CAUSE_ORDER:
        rows = by_cause.get(cause, [])
        if not rows:
            continue
        title, desc = SECTION_TITLES.get(cause, (cause.capitalize(), "Known gaps."))
        fences = []
        for mod, construct, code, msg in sorted(rows, key=lambda r: (r[0], r[1], r[2])):
            fences.append(
                {
                    "module": mod,
                    "construct": construct,
                    "code": code,
                    "diagnostic": msg,
                }
            )
        causes.append(
            {
                "id": cause,
                "title": title,
                "description": desc,
                "fences": fences,
            }
        )

    pinned: dict[str, Any] | None = None
    if pin and pin.exists():
        try:
            pinned = json.loads(pin.read_text(encoding="utf-8"))
        except Exception:
            pinned = None

    data_doc = {
        "schema": "1",
        "generated": (pinned.get("generated") if pinned else None) or measured_date,
        "commit": (pinned.get("commit") if pinned else None) or _git("rev-parse", "HEAD"),
        "sources": {
            "corpus": "benchmarks/real_scripts/scripts",
            "report": "benchmarks/real_scripts/REPORT.md",
        },
        "summary": {
            "scripts": script_count,
            "total_fences": sum(len(c["fences"]) for c in causes),
            "unique_codes": sorted(codes_seen),
        },
        "causes": causes,
    }
    content = json.dumps(data_doc, indent=2) + "\n"
    return {str(DEFAULT_DATA): content}, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit with non-zero status if generated content differs")
    parser.add_argument("--out", type=Path, default=DEFAULT_DATA, help="output path for what_will_fail.json")
    parser.add_argument("--date", type=str, default=None, help="override the measured date (YYYY-MM-DD)")
    args = parser.parse_args()

    files, errors = build(date=args.date, pin=args.out if args.check else None)
    if errors:
        for err in errors:
            print(f"error: {err}", file=sys.stderr)
        return 2

    content = files[str(DEFAULT_DATA)]
    if args.check:
        target = args.out
        if not target.exists():
            print(f"{target} does not exist", file=sys.stderr)
            return 1
        existing = target.read_text(encoding="utf-8")
        if existing != content:
            print(f"{target} is out of date. Run: uv run python scripts/gen_what_will_fail.py", file=sys.stderr)
            return 1
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
