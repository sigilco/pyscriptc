"""Build `site/compat-data/compat.json`, the data file behind the compat site.

The builder reads the data the compiler already produces and writes one JSON
document. It never reads the holdout corpus and never writes outside the
output path.

    uv run python scripts/build_compat_json.py [--out PATH] [--score PATH] [--check]

Sources:

| section | source |
| --- | --- |
| builtins, stdlib | `scripts/support_data.toml`, probed through the compiler |
| packages | `benchmarks/compat/top100/pins.json` plus `pyscriptc coverage` |
| language | the compat2 scorer output over the visible probes |
| fences | the same package measurement, aggregated per `PSxxxx` code |
| history | the previous `compat.json` plus the current measurement |

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.compat2.census import DEFAULT_SRC  # noqa: E402
from scripts.compat_schema import HISTORY_LIMIT, SCHEMA_VERSION, validate  # noqa: E402
from scripts.gen_support_table import evaluate  # noqa: E402

from pyscriptc.coverage import build_report  # noqa: E402
from pyscriptc.diagnostics import DIAGNOSTICS  # noqa: E402

DATA = ROOT / "scripts" / "support_data.toml"
PINS = ROOT / "benchmarks" / "compat" / "top100" / "pins.json"
DEFAULT_OUT = ROOT / "site" / "compat-data" / "compat.json"

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"
STATUS_FULL, STATUS_PARTIAL, STATUS_NONE = "full", "partial", "none"

# Where the scorer output lands when the nightly runs it.
DEFAULT_SCORE = ROOT / "nightly" / "visible-score.json"

# The language-feature groups the site shows. Each is a prefix of a compat2
# probe id. A group with no probes is skipped.
LANGUAGE_GROUPS = (
    ("lang.", "language"),
    ("builtins.", "builtins"),
    ("stdlib.", "stdlib modules"),
    ("surface.", "stdlib surface"),
    ("top100.", "PyPI packages"),
)


def _status_for(status: str) -> str:
    """Map the emoji status from `gen_support_table.evaluate` to the site vocabulary."""
    if status == GREEN:
        return STATUS_FULL
    if status == YELLOW:
        return STATUS_PARTIAL
    return STATUS_NONE


def _slug(label: str) -> str:
    out = []
    for char in label.lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except OSError, subprocess.CalledProcessError:
        return ""
    return proc.stdout.strip()


def _commit() -> str:
    """The SHA of the tree being measured, empty when it cannot be read."""
    return _git("rev-parse", "HEAD")


def _generated(date: str | None) -> str:
    """The measurement date. `date` wins, so the caller controls it."""
    if date:
        return date
    return _git("show", "-s", "--format=%cd", "--date=format:%Y-%m-%d", "HEAD")


def _support_rows(data: dict[str, Any], kind: str, errors: list[str]) -> list[dict[str, Any]]:
    """Rows for the builtins or the stdlib section."""
    rows: list[dict[str, Any]] = []
    for entry in data[kind]:
        result = evaluate(entry, kind, errors)
        names = list(entry["names"])
        label = result["label"]
        missing = result["missing"]
        unsupported = [] if result["status"] == GREEN else [missing]
        rows.append(
            {
                "id": f"{kind}.{_slug(names[0])}",
                "kind": kind,
                "label": label,
                "names": names,
                "status": _status_for(result["status"]),
                "count": result["count"],
                "unsupported": unsupported,
                "fences": sorted(result["codes"]),
                "note": str(entry.get("note", "")),
            }
        )
    return rows


def _package_rows() -> list[dict[str, Any]]:
    """One row per pinned package, measured with `pyscriptc coverage`.

    Returns the package rows and the fence-code counters from the same
    measurement, so the two sections can never disagree.
    """
    pins = json.loads(PINS.read_text(encoding="utf-8"))["packages"]
    versions = {p["import_name"]: (name, p["version"]) for name, p in pins.items()}
    rows: list[dict[str, Any]] = []
    code_counts: Counter[str] = Counter()
    code_packages: Counter[str] = Counter()
    for tree in sorted(DEFAULT_SRC.iterdir()):
        if not tree.is_dir():
            continue
        report = build_report(tree)
        summary = report["summary"]
        roots = Counter(f["family"] for f in report["fences"] if not f["derived"])
        codes = Counter(f["code"] for f in report["fences"])
        code_counts.update(codes)
        code_packages.update(set(codes))
        definitions = summary["definitions"]
        fenced = summary["fencedDefinitions"]
        if summary["fences"] == 0:
            status = STATUS_FULL
        elif fenced * 2 <= definitions:
            status = STATUS_PARTIAL
        else:
            status = STATUS_NONE
        name, version = versions.get(tree.name, (tree.name, "?"))
        rows.append(
            {
                "name": name,
                "import_name": tree.name,
                "version": version,
                "status": status,
                "fences": summary["fences"],
                "definitions": definitions,
                "fenced_definitions": fenced,
                "top_families": [{"family": family, "count": count} for family, count in roots.most_common(3)],
                "codes": [{"code": code, "count": count} for code, count in codes.most_common(3)],
                "pypi": f"https://pypi.org/project/{name}/",
            }
        )
    fence_rows = _fence_rows(code_counts, code_packages)
    return rows, fence_rows


def _fence_rows(counts: Counter[str], packages: Counter[str]) -> list[dict[str, Any]]:
    """Fence-code counters from the package measurement.

    `counts` maps a code to how often the pinned trees emit it, `packages`
    maps a code to how many trees carry it.
    """
    rows = []
    for code, count in counts.most_common():
        entry = DIAGNOSTICS.get(code)
        rows.append(
            {
                "code": code,
                "title": entry.title if entry else "",
                "count": count,
                "packages": packages[code],
            }
        )
    return rows


def _language_rows(score: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Language-feature groups, aggregated from the scorer output."""
    if not score:
        return []
    rows_by_id = {row["id"]: row for row in score.get("rows", [])}
    weights = score.get("weights", {})
    rows: list[dict[str, Any]] = []
    for prefix, label in LANGUAGE_GROUPS:
        matched = [row for row in rows_by_id.values() if row["id"].startswith(prefix)]
        if not matched:
            continue
        outcomes = Counter(row["outcome"] for row in matched)
        weighted = sum(weights.get(row["id"], 1.0) for row in matched if row["outcome"] == "pass")
        fence_codes = Counter(row["code"] for row in matched if row["outcome"] == "fence" and row.get("code"))
        rows.append(
            {
                "id": prefix.rstrip("."),
                "label": label,
                "probes": len(matched),
                "outcomes": {name: outcomes.get(name, 0) for name in sorted(outcomes)},
                "weighted_pass": round(weighted, 3),
                "fence_codes": dict(sorted(fence_codes.items(), key=lambda kv: (-kv[1], kv[0]))),
            }
        )
    return rows


def _history(previous: dict[str, Any] | None, point: dict[str, Any]) -> list[dict[str, Any]]:
    """The previous history plus the current point, capped at the limit."""
    history: list[dict[str, Any]] = []
    if previous:
        history = [row for row in previous.get("history", []) if isinstance(row, dict)]
    dates = {row.get("date") for row in history}
    if point["date"] in dates:
        history = [row for row in history if row.get("date") != point["date"]]
    history.append(point)
    history.sort(key=lambda row: row.get("date", ""))
    return history[-HISTORY_LIMIT:]


def build(
    score_path: Path | None = None,
    previous_path: Path | None = None,
    date: str | None = None,
    pin: Path | None = None,
) -> dict[str, Any]:
    """Build the document. Raises ValueError when a source is missing or invalid.

    `pin` is a previous document whose `generated` date and `commit` are
    reused, so a `--check` run over an unchanged tree sees the same bytes.
    """
    data = tomllib.loads(DATA.read_text(encoding="utf-8"))
    errors: list[str] = []
    builtins = _support_rows(data, "builtin", errors)
    stdlib = _support_rows(data, "stdlib", errors)
    if errors:
        raise ValueError("support probe errors:\n" + "\n".join(errors))
    packages, fences = _package_rows()
    score = _load_score(score_path)
    language = _language_rows(score)
    previous = _load_previous(previous_path)
    pinned = _load_previous(pin) if pin else None
    commit = pinned["commit"] if pinned else _commit()
    generated = pinned["generated"] if pinned else _generated(date)
    packages_full = sum(1 for row in packages if row["status"] == STATUS_FULL)
    packages_partial = sum(1 for row in packages if row["status"] == STATUS_PARTIAL)
    packages_none = sum(1 for row in packages if row["status"] == STATUS_NONE)
    weighted_pass = 0.0
    if score:
        weights = score.get("weights", {})
        weighted_pass = round(
            sum(weights.get(row["id"], 1.0) for row in score.get("rows", []) if row["outcome"] == "pass"), 3
        )
    point = {
        "date": generated,
        "commit": commit,
        "weighted_pass": weighted_pass,
        "packages_full": packages_full,
        "packages_partial": packages_partial,
        "packages_none": packages_none,
        "fences": sum(row["fences"] for row in packages),
    }
    document = {
        "schema": SCHEMA_VERSION,
        "generated": generated,
        "commit": commit,
        "sources": {
            "support_data": "scripts/support_data.toml",
            "pins": "benchmarks/compat/top100/pins.json",
            "coverage": "pyscriptc coverage over benchmarks/compat/top100/src",
            "score": _relative(score_path),
        },
        "summary": {
            "builtins_full": sum(1 for row in builtins if row["status"] == STATUS_FULL),
            "builtins_partial": sum(1 for row in builtins if row["status"] == STATUS_PARTIAL),
            "builtins_none": sum(1 for row in builtins if row["status"] == STATUS_NONE),
            "stdlib_full": sum(1 for row in stdlib if row["status"] == STATUS_FULL),
            "stdlib_partial": sum(1 for row in stdlib if row["status"] == STATUS_PARTIAL),
            "stdlib_none": sum(1 for row in stdlib if row["status"] == STATUS_NONE),
            "packages_full": packages_full,
            "packages_partial": packages_partial,
            "packages_none": packages_none,
            "weighted_pass": weighted_pass,
            "fences": point["fences"],
        },
        "builtins": builtins,
        "stdlib": stdlib,
        "packages": packages,
        "language": language,
        "fences": fences,
        "history": _history(previous, point),
    }
    problems = validate(document)
    if problems:
        raise ValueError("the document does not match the schema:\n" + "\n".join(problems))
    return document


def _load_score(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path | None) -> str:
    """A path relative to the repository root, so the file does not leak a home directory."""
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _load_previous(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def render(document: dict[str, Any]) -> str:
    """The canonical text of the document: two-space indent, one trailing newline."""
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


# Sections a `--check` compares. The generated date and the commit change with
# every run, so the check compares what the tree measures and leaves the
# run-time stamps to the nightly.
_MEASURED_SECTIONS = ("builtins", "stdlib", "packages", "fences", "language", "summary")


def check(document: dict[str, Any], out: Path, measured: bool = False) -> list[str]:
    """Problems when the committed file disagrees with the tree.

    `measured=True` (a `--score` run) compares the run-stamped top level too:
    the nightly is about to overwrite the file, so its date and commit must be
    part of the diff.
    """
    if not out.exists():
        return [f"missing: {out} (run scripts/build_compat_json.py)"]
    try:
        committed = json.loads(out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"{out} is not valid JSON: {error}"]
    problems: list[str] = []
    if not measured:
        document = {key: value for key, value in document.items() if key in _MEASURED_SECTIONS}
        committed = {key: value for key, value in committed.items() if key in _MEASURED_SECTIONS}
    if document != committed:
        differing = sorted(key for key in set(document) | set(committed) if document.get(key) != committed.get(key))
        problems.append(f"stale (run scripts/build_compat_json.py): {out}, sections differ: {', '.join(differing)}")
    return problems


def write_atomic(path: Path, text: str) -> None:
    """Write the text so an interrupted run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the compat site data file.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path")
    parser.add_argument("--score", type=Path, default=None, help="compat2 scorer output to read")
    parser.add_argument("--previous", type=Path, default=None, help="previous compat.json, for the history")
    parser.add_argument("--date", default=None, help="measurement date (YYYY-MM-DD)")
    parser.add_argument("--check", action="store_true", help="fail when the measured sections differ from the file")
    args = parser.parse_args(argv)
    previous = args.previous if args.previous is not None else args.out
    try:
        document = build(score_path=args.score, previous_path=previous, date=args.date)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.check:
        problems = check(document, args.out, measured=bool(args.score))
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"current: {args.out}")
        return 0
    write_atomic(args.out, render(document))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
