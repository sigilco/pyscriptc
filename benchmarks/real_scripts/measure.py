"""Measure what real scripts fail on today, and rank the blockers.

This is the R0 rung: the pass rate of third-party real-world scripts, the
population a beta tester brings. The runner answers three questions:

1. Does the file lower to IR with zero fences?
2. When it does not, which fence family and which stdlib module hold it back?
3. How many scripts does each blocker release, if it is fixed?

The third question is the ranking. A blocker that holds back five scripts is
worth more than one that holds back one, and a blocker that is a single
missing method is worth more than one that needs an epic. The tool ranks by
scripts released per unit of effort, so the work list is ordered by payoff
per change, not by fence count.

The runner lives outside the frozen v2 gates. It never writes to
`benchmarks/compat2/`, it never reads the scorer holdout, and it does not
change how `compile` treats a fence.

    uv run python -m benchmarks.real_scripts.measure            # the table
    uv run python -m benchmarks.real_scripts.measure --json out.json
    uv run python -m benchmarks.real_scripts.measure --markdown REPORT.md
    uv run python -m benchmarks.real_scripts.measure --only cat,folder_size
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyscriptc.coverage import build_report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = HERE / "scripts"
MANIFEST = HERE / "manifest.json"

# A blocker family maps to the size of the change that releases it. The sizes
# are the ones the repo uses: S under a day, M one to three days, L about a
# week, XL more. The value is the estimate for the family, not for one script.
EFFORT: dict[str, str] = {
    "name-resolution": "L",
    "construct-other": "L",
    "call-arity": "S",
    "keyword-args": "M",
    "local-inference": "S",
    "return-inference": "S",
    "param-inference": "S",
    "generic-alias": "M",
    "stdlib:sys": "M",
    "stdlib:datetime": "M",
    "stdlib:os": "M",
    "stdlib:re": "L",
    "stdlib:json": "M",
    "stdlib:csv": "M",
    "stdlib:pathlib": "M",
    "stdlib:argparse": "M",
    "stdlib:shutil": "M",
    "stdlib:subprocess": "M",
    "stdlib:collections": "M",
    "stdlib:itertools": "M",
    "stdlib:functools": "M",
    "stdlib:logging": "M",
    "stdlib:dataclasses": "M",
    "stdlib:urllib": "M",
    "stdlib:hashlib": "S",
    "stdlib:textwrap": "S",
    "stdlib:tempfile": "S",
    "stdlib:zipfile": "M",
    "stdlib:tarfile": "M",
    "stdlib:configparser": "M",
    "stdlib:glob": "S",
    "operator": "M",
    "import-table": "M",
    "class-body": "M",
    "decorators": "M",
    "parameter-forms": "M",
    "except-as": "M",
    "with-as": "M",
    "enum": "M",
    "module-statements": "S",
    "module-assign": "S",
    "unlowered-call": "L",
    "type-mismatch": "M",
    "value-type": "M",
    "union-types": "XL",
    "optional-types": "XL",
    "dynamic-annotation": "M",
    "forward-reference": "S",
    "unlowered-type-name": "M",
    "uninitialized-field": "S",
    "field-lookup": "S",
    "fall-off-end": "S",
    "annotation-other": "M",
    "module-value": "M",
    "typing-value": "S",
    "non-latin1": "S",
    "generators": "XL",
    "contextmanager": "M",
    "async": "XL",
    "stdlib": "M",
    "queued-stdlib": "M",
    "import-cycle": "S",
    "construct-other-stdlib": "M",
}

# Days per effort size. The ranking divides by this, so a one-day blocker
# that releases ten scripts beats a one-week blocker that releases four.
DAYS: dict[str, float] = {"S": 1.0, "M": 2.0, "L": 5.0, "XL": 15.0}

# The root cause a family belongs to. Fences cascade: when a local is never
# declared, the assignment is skipped and every later read of that name
# fences as name-resolution. Ranking the families by fence count therefore
# ranks the symptom. The root cause is the epic that has to land.
ROOT_CAUSE: dict[str, str] = {
    "param-inference": "inference",
    "return-inference": "inference",
    "local-inference": "inference",
    "name-resolution": "inference",
    "annotation-other": "inference",
    "unlowered-type-name": "inference",
    "dynamic-annotation": "inference",
    "forward-reference": "inference",
    "generic-alias": "inference",
    "union-types": "inference",
    "optional-types": "inference",
    "unlowered-call": "call-table",
    "call-arity": "call-table",
    "keyword-args": "call-table",
    "stdlib:os": "stdlib-surface",
    "stdlib:sys": "stdlib-surface",
    "stdlib:time": "stdlib-surface",
    "stdlib:json": "stdlib-surface",
    "stdlib:csv": "stdlib-surface",
    "stdlib:re": "stdlib-surface",
    "stdlib:pathlib": "stdlib-surface",
    "stdlib:argparse": "stdlib-surface",
    "stdlib:shutil": "stdlib-surface",
    "stdlib:subprocess": "stdlib-surface",
    "stdlib:collections": "stdlib-surface",
    "stdlib:itertools": "stdlib-surface",
    "stdlib:functools": "stdlib-surface",
    "stdlib:logging": "stdlib-surface",
    "stdlib:dataclasses": "stdlib-surface",
    "stdlib:urllib": "stdlib-surface",
    "stdlib:random": "stdlib-surface",
    "stdlib:tempfile": "stdlib-surface",
    "stdlib:hashlib": "stdlib-surface",
    "stdlib:textwrap": "stdlib-surface",
    "stdlib:zipfile": "stdlib-surface",
    "stdlib:tarfile": "stdlib-surface",
    "stdlib:configparser": "stdlib-surface",
    "stdlib:glob": "stdlib-surface",
    "stdlib:datetime": "stdlib-surface",
    "operator": "operators",
    "construct-other": "statements",
    "module-assign": "statements",
    "module-statements": "statements",
    "import-table": "imports",
    "import-cycle": "imports",
    "class-body": "classes",
    "decorators": "classes",
    "parameter-forms": "classes",
    "except-as": "exceptions",
    "with-as": "exceptions",
    "enum": "classes",
    "field-lookup": "classes",
    "uninitialized-field": "classes",
    "generators": "generators",
    "contextmanager": "exceptions",
    "async": "async",
    "type-mismatch": "checker",
    "value-type": "checker",
    "module-value": "modules",
    "typing-value": "modules",
    "non-latin1": "strings",
    "fall-off-end": "statements",
}

# The score a module name gets in a fence message. The messages the compiler
# emits name the module in prose, so the module is read out of the text.
_MODULE_IN_MESSAGE = re.compile(r"(?:\b(?:of|from|by|to|for)\s+)?\b([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\b")


@dataclass
class ScriptResult:
    """The outcome for one real script."""

    slug: str
    path: str
    compile_pass: bool
    fences: int
    root_fences: int
    definitions: int
    fenced_definitions: int
    families: list[tuple[str, int]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)
    modules: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "path": self.path,
            "compilePass": self.compile_pass,
            "fences": self.fences,
            "rootFences": self.root_fences,
            "definitions": self.definitions,
            "fencedDefinitions": self.fenced_definitions,
            "families": [{"family": name, "fences": count} for name, count in self.families],
            "codes": self.codes,
            "modules": self.modules,
            "error": self.error,
        }


def script_paths(root: Path) -> list[Path]:
    """Every vendored script, sorted by slug."""

    return sorted(root.glob("*.py"))


def _module_of(message: str) -> str:
    """The stdlib module a fence message names, or an empty string."""

    match = re.search(r"lowered subset of ([a-z][\w.]*)", message)
    if match:
        return match.group(1)
    match = re.search(r"([a-z][\w.]*) is not in the closed import table", message)
    if match:
        return match.group(1)
    return ""


def analyse(path: Path) -> ScriptResult:
    """Run the coverage report over one script and classify the outcome."""

    try:
        report = build_report(path)
    except Exception as exc:  # a fence that escapes the report is still a fence
        return ScriptResult(
            slug=path.stem,
            path=str(path),
            compile_pass=False,
            fences=1,
            root_fences=1,
            definitions=0,
            fenced_definitions=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    summary: dict[str, Any] = report["summary"]
    families: Counter[str] = Counter()
    modules: list[str] = []
    messages: list[str] = []
    for fence in report["fences"]:
        if fence["derived"]:
            continue
        families[fence["family"]] += 1
        messages.append(f"{fence['code']}: {fence['message']}")
        module = _module_of(fence["message"])
        if module and module not in modules:
            modules.append(module)
    ordered = sorted(families.items(), key=lambda item: (-item[1], item[0]))
    return ScriptResult(
        slug=path.stem,
        path=str(path),
        compile_pass=summary["fences"] == 0,
        fences=summary["fences"],
        root_fences=summary["rootFences"],
        definitions=summary["definitions"],
        fenced_definitions=summary["fencedDefinitions"],
        families=ordered,
        codes=summary["byCode"],
        modules=modules,
        messages=messages,
    )


def analyse_all(root: Path, only: list[str] | None = None) -> list[ScriptResult]:
    """Analyse every script, optionally restricted to the given slugs."""

    wanted = set(only) if only else None
    results: list[ScriptResult] = []
    for path in script_paths(root):
        if wanted is not None and path.stem not in wanted:
            continue
        results.append(analyse(path))
    return results


def _effort_of(family: str) -> str:
    if family in EFFORT:
        return EFFORT[family]
    if family.startswith("stdlib:"):
        return "M"
    return "L"


def blocker_ranking(results: list[ScriptResult]) -> list[dict[str, Any]]:
    """Rank the blockers by scripts released per day of work.

    Two counts, and they answer different questions:

    - `scriptsBlocked` is the touch count: how many scripts carry this family.
      It overstates, because a script with five blockers stays blocked until
      all five land.
    - `scriptsAlone` is the release count: how many scripts this family
      holds back on its own, with no other family in the way. Fixing it
      releases exactly those.

    The score is `scriptsAlone` per day of work. It is the honest payoff of
    one change, and it is what the grind plan is ordered by.
    """

    per_script: list[tuple[str, list[tuple[str, int]]]] = [
        (result.slug, result.families) for result in results if not result.compile_pass
    ]
    blocked: dict[str, set[str]] = defaultdict(set)
    for slug, families in per_script:
        for family, _count in families:
            blocked[family].add(slug)

    ranking: list[dict[str, Any]] = []
    for family, slugs in blocked.items():
        effort = _effort_of(family)
        days = DAYS[effort]
        alone = sum(1 for _slug, families in per_script if [name for name, _c in families] == [family])
        ranking.append(
            {
                "family": family,
                "rootCause": ROOT_CAUSE.get(family, "other"),
                "scriptsBlocked": len(slugs),
                "scriptsAlone": alone,
                "effort": effort,
                "days": days,
                "scriptsPerDay": round(alone / days, 3),
                "slugs": sorted(slugs),
            }
        )
    ranking.sort(key=lambda item: (-item["scriptsPerDay"], -item["scriptsAlone"], item["family"]))
    return ranking


def root_cause_ranking(results: list[ScriptResult]) -> list[dict[str, Any]]:
    """Rank the root causes: the epics that have to land to release scripts.

    A script is released by a root cause when every family it carries
    belongs to that cause. The number is the size of the prize for the
    epic.
    """

    per_script: list[tuple[str, list[str]]] = [
        (result.slug, [name for name, _count in result.families]) for result in results
    ]
    causes: dict[str, set[str]] = defaultdict(set)
    for slug, families in per_script:
        cause = ROOT_CAUSE.get(families[0], "other") if families else ""
        if not cause:
            continue
        if all(ROOT_CAUSE.get(name, "other") == cause for name in families):
            causes[cause].add(slug)
    ranking = [
        {
            "cause": cause,
            "scriptsReleased": len(slugs),
            "scripts": sorted(slugs),
        }
        for cause, slugs in causes.items()
    ]
    ranking.sort(key=lambda item: (-item["scriptsReleased"], item["cause"]))
    return ranking


def counterfactual(results: list[ScriptResult], fixed: set[str]) -> dict[str, Any]:
    """How many scripts would compile if the given families were fixed.

    This is an upper bound read off the fence families a script carries: it
    assumes every listed family lands and that nothing new surfaces
    underneath. It is a planning number, not a measurement. The measured
    number is the corpus pass rate after the change.
    """

    released: list[str] = []
    for result in results:
        if result.compile_pass:
            released.append(result.slug)
            continue
        families = {name for name, _count in result.families}
        if families and families <= fixed:
            released.append(result.slug)
    return {"fixed": sorted(fixed), "released": sorted(released), "count": len(released)}


def module_ranking(results: list[ScriptResult]) -> list[dict[str, Any]]:
    """Rank the stdlib modules by the scripts they hold back."""

    blocked: dict[str, set[str]] = defaultdict(set)
    for result in results:
        if result.compile_pass:
            continue
        for module in result.modules:
            blocked[module].add(result.slug)
    ranking = [
        {"module": module, "scriptsBlocked": len(slugs), "slugs": sorted(slugs)} for module, slugs in blocked.items()
    ]
    ranking.sort(key=lambda item: (-item["scriptsBlocked"], item["module"]))
    return ranking


def import_census(results: list[ScriptResult]) -> list[dict[str, Any]]:
    """Count the stdlib modules the corpus imports, and how often they block.

    The import count is the demand signal: how many scripts want the module.
    The blocked count is the supply gap: how many of those hit a fence.
    """

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"scripts": []}
    imports: Counter[str] = Counter()
    blocked_by: dict[str, set[str]] = defaultdict(set)
    for entry in manifest.get("scripts", []):
        path = HERE / entry["file"]
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.M):
            name = match.group(1).split(".")[0]
            imports[name] += 1
    for result in results:
        if result.compile_pass:
            continue
        for module in result.modules:
            blocked_by[module].add(result.slug)
    rows: list[dict[str, Any]] = []
    for module, count in imports.most_common():
        rows.append(
            {
                "module": module,
                "scriptsImporting": count,
                "scriptsBlocked": len(blocked_by.get(module, set())),
            }
        )
    return rows


def render_table(results: list[ScriptResult]) -> str:
    """The per-script table."""

    lines = [
        "| script | compile | fences | top blocking features |",
        "| --- | --- | --- | --- |",
    ]
    for result in results:
        status = "pass" if result.compile_pass else "fenced"
        if result.error:
            features = f"error: {result.error}"
        elif result.compile_pass:
            features = "-"
        else:
            features = ", ".join(f"{family} ({count})" for family, count in result.families[:3])
        lines.append(f"| {result.slug} | {status} | {result.fences} | {features} |")
    return "\n".join(lines)


def render_report(results: list[ScriptResult]) -> str:
    """The whole markdown report: summary, rankings and the per-script table."""

    total = len(results)
    passed = sum(1 for result in results if result.compile_pass)
    fences = sum(result.fences for result in results)
    ranking = blocker_ranking(results)
    causes = root_cause_ranking(results)
    modules = module_ranking(results)
    lines = [
        "# Real script report",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} by the measurement tool.",
        "",
        "Command: `uv run python -m benchmarks.real_scripts.measure --markdown benchmarks/real_scripts/REPORT.md`.",
        "",
        f"Rung R0: {passed} of {total} vendored third-party scripts lower with zero fences "
        f"({100 * passed // max(total, 1)}%). The corpus holds {fences} fences in total.",
        "",
        "The corpus is third-party source, vendored with a pinned commit and the upstream licence in "
        "`licenses/`. The manifest records the URL, the commit and the sha256 of every file. The scripts "
        "are not edited: they are measured as they are, so a fence names a real gap in the compiler and "
        "not a mistake in the fixture.",
        "",
        "## Blockers, ranked",
        "",
        "How many scripts each blocker touches, and how many it releases on its own. The score is the "
        "scripts released per day of work at the size the estimate gives. The rank is the work list.",
        "",
        "| blocker | root cause | scripts touched | released alone | size | scripts/day |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in ranking:
        lines.append(
            f"| {row['family']} | {row['rootCause']} | {row['scriptsBlocked']} | {row['scriptsAlone']} | "
            f"{row['effort']} | {row['scriptsPerDay']} |"
        )
    if not ranking:
        lines.append("| none | - | 0 | 0 | - | 0 |")
    lines += [
        "",
        "## Root causes, ranked",
        "",
        "The families cascade: a name that is never declared fences as name-resolution on every later "
        "read, so the fence count ranks the symptom. A script is released by the root cause when every "
        "family it carries belongs to that cause. These are the epics, and the prize each one holds.",
        "",
        "| root cause | scripts released |",
        "| --- | --- |",
    ]
    for row in causes:
        lines.append(f"| {row['cause']} | {row['scriptsReleased']} |")
    if not causes:
        lines.append("| none | 0 |")
    lines += [
        "",
        "## Stdlib modules, ranked",
        "",
        "The stdlib module each fence names, and how many scripts it holds back. A module with a high "
        "demand count and a low blocked count is already first class; a module with a high demand count "
        "and a high blocked count is the next grind.",
        "",
        "| module | scripts blocked |",
        "| --- | --- |",
    ]
    for row in modules:
        lines.append(f"| {row['module']} | {row['scriptsBlocked']} |")
    if not modules:
        lines.append("| none | 0 |")
    lines += [
        "",
        "## Scripts",
        "",
        render_table(results),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="real_scripts", description=__doc__.splitlines()[0])
    parser.add_argument("--scripts", type=Path, default=SCRIPTS, help="the vendored script directory")
    parser.add_argument("--only", help="comma separated slugs to analyse")
    parser.add_argument("--markdown", type=Path, help="write the markdown report to this path")
    parser.add_argument("--json", type=Path, help="write the JSON results to this path")
    parser.add_argument("--quiet", action="store_true", help="print nothing, only write the outputs")
    args = parser.parse_args(argv)
    only: list[str] | None = None
    if args.only:
        only = [item.strip() for item in args.only.split(",") if item.strip()]
    results = analyse_all(args.scripts, only)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "generated": datetime.now(UTC).date().isoformat(),
                    "scripts": [result.to_json() for result in results],
                    "blockers": blocker_ranking(results),
                    "rootCauses": root_cause_ranking(results),
                    "modules": module_ranking(results),
                    "imports": import_census(results),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_report(results), encoding="utf-8")
    if not args.quiet:
        print(render_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
