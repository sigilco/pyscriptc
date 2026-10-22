"""Tree corpus runner: the R2 rung of the adoption ladder.

R2 is the multi-module rung: a directory whose entry is `__main__.py`, whose
siblings are plain imports, and which compiles into one native binary. The
metric is the share of trees whose entry compiles and whose binary matches
the CPython recording on stdout and exit code.

    uv run python -m benchmarks.scripts_corpus.trees            # the table
    uv run python -m benchmarks.scripts_corpus.trees --exec     # build and run
    uv run python -m benchmarks.scripts_corpus.trees --json out.json

Each tree lives in `trees/<slug>/` with a `meta.json` beside the code. The
recorded CPython output lives in `oracle/<slug>.txt` and `oracle/<slug>.json`,
the same convention the R1 scripts use.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyscriptc.coverage import build_report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TREES = HERE / "trees"
ORACLE = HERE / "oracle"

OUTCOME_PASS = "pass"
OUTCOME_WRONG_OUTPUT = "wrong-output"
OUTCOME_BUILD_ERROR = "build-error"


@dataclass
class TreeResult:
    """The outcome for one corpus tree."""

    slug: str
    path: str
    compile_pass: bool
    fences: int
    root_fences: int
    definitions: int
    fenced_definitions: int
    modules: list[str] = field(default_factory=list)
    top_families: list[tuple[str, int]] = field(default_factory=list)
    error: str = ""
    # Set by the `--exec` stage: the R2 outcome of running the compiled binary.
    exec_outcome: str = ""
    exec_detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "path": self.path,
            "compilePass": self.compile_pass,
            "fences": self.fences,
            "rootFences": self.root_fences,
            "definitions": self.definitions,
            "fencedDefinitions": self.fenced_definitions,
            "modules": self.modules,
            "topFamilies": [{"family": family, "fences": count} for family, count in self.top_families],
            "error": self.error,
            "execOutcome": self.exec_outcome,
            "execDetail": self.exec_detail,
        }


def tree_dirs(root: Path) -> list[Path]:
    """Every corpus tree, in slug order."""
    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "__main__.py").is_file())


def analyse(path: Path) -> TreeResult:
    """Run the coverage report over one tree and classify the outcome."""

    meta: dict[str, Any] = {}
    meta_path = path / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    try:
        report = build_report(path)
    except Exception as exc:  # a fence that escapes the report is still a fence
        return TreeResult(
            slug=path.name,
            path=str(path),
            compile_pass=False,
            fences=1,
            root_fences=1,
            definitions=0,
            fenced_definitions=0,
            modules=meta.get("modules", []),
            error=f"{type(exc).__name__}: {exc}",
        )
    summary: dict[str, Any] = report["summary"]
    fences: list[dict[str, Any]] = report["fences"]
    root_families: dict[str, int] = {}
    for fence in fences:
        if fence["derived"]:
            continue
        family: str = fence["family"]
        root_families[family] = root_families.get(family, 0) + 1
    ordered: list[tuple[str, int]] = sorted(root_families.items(), key=lambda item: (-item[1], item[0]))
    return TreeResult(
        slug=path.name,
        path=str(path),
        compile_pass=summary["fences"] == 0,
        fences=summary["fences"],
        root_fences=summary["rootFences"],
        definitions=summary["definitions"],
        fenced_definitions=summary["fencedDefinitions"],
        modules=meta.get("modules", []),
        top_families=ordered,
    )


def analyse_all(root: Path, only: list[str] | None = None) -> list[TreeResult]:
    """Analyse every tree, optionally restricted to the given slugs."""

    wanted = set(only) if only else None
    results: list[TreeResult] = []
    for path in tree_dirs(root):
        if wanted is not None and path.name not in wanted:
            continue
        results.append(analyse(path))
    return results


def build_tree(source: Path, out_dir: Path) -> tuple[Path | None, str]:
    """Build the tree into one native binary. Returns the path or the error."""

    binary: Path = out_dir / source.name
    completed = subprocess.run(
        [
            "uv",
            "run",
            "pyscriptc",
            "compile",
            str(source),
            "--emit",
            "bin",
            "--skip-check",
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if completed.returncode != 0:
        return None, (completed.stderr.strip() or completed.stdout.strip() or "compile failed")
    return binary, ""


def recorded_invocation(slug: str) -> list[str]:
    """The recorded argv of a tree, from its meta.json."""

    meta = json.loads((TREES / slug / "meta.json").read_text(encoding="utf-8"))
    from benchmarks.scripts_corpus.oracle import fixtures_dir

    return [arg.replace("{fixtures}", str(fixtures_dir())) for arg in meta.get("invocation", [])]


def exec_tree(result: TreeResult, argv: list[str], oracle: Path) -> None:
    """Compile the tree, run the binary, and compare with the CPython recording.

    A fenced tree has nothing to run: the outcome stays empty and the caller
    renders it as `-`.
    """

    if not result.compile_pass:
        result.exec_outcome = ""
        result.exec_detail = ""
        return
    recorded_path: Path = oracle / f"{result.slug}.txt"
    if not recorded_path.is_file():
        result.exec_outcome = ""
        result.exec_detail = "no recorded oracle"
        return
    recorded: str = recorded_path.read_text(encoding="utf-8")
    recorded_meta: Path = oracle / f"{result.slug}.json"
    recorded_code: int = 0
    if recorded_meta.is_file():
        recorded_code = int(json.loads(recorded_meta.read_text()).get("exitCode", 0))
    with tempfile.TemporaryDirectory(prefix="pyscriptc-trees-") as tmp:
        binary, error = build_tree(Path(result.path), Path(tmp))
        if binary is None:
            result.exec_outcome = OUTCOME_BUILD_ERROR
            result.exec_detail = error.splitlines()[-1] if error else "compile failed"
            return
        completed = subprocess.run([str(binary), *argv], capture_output=True, text=True, check=False)
        code, stdout = completed.returncode, completed.stdout
    if code != recorded_code:
        result.exec_outcome = OUTCOME_WRONG_OUTPUT
        result.exec_detail = f"exit {code}, CPython exits {recorded_code}"
        return
    if stdout != recorded:
        result.exec_outcome = OUTCOME_WRONG_OUTPUT
        result.exec_detail = f"stdout differs ({len(stdout)} bytes, CPython has {len(recorded)})"
        return
    result.exec_outcome = OUTCOME_PASS
    result.exec_detail = ""


def family_ranking(results: list[TreeResult]) -> list[tuple[str, int]]:
    """How many trees each blocking family holds back, most first."""

    blocking: dict[str, int] = {}
    for result in results:
        if result.compile_pass:
            continue
        for family, _count in result.top_families:
            blocking[family] = blocking.get(family, 0) + 1
    return sorted(blocking.items(), key=lambda item: (-item[1], item[0]))


def render_table(results: list[TreeResult], with_exec: bool = False) -> str:
    """The per-tree table: compile pass, fence count, top blocking features."""

    if with_exec:
        lines = [
            "| tree | compile | fences | exec | detail | top blocking features |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    else:
        lines = [
            "| tree | compile | fences | top blocking features |",
            "| --- | --- | --- | --- |",
        ]
    for result in results:
        status: str = "pass" if result.compile_pass else "fenced"
        if result.error:
            features: str = f"error: {result.error}"
        elif result.compile_pass:
            features = "-"
        else:
            features = ", ".join(f"{family} ({count})" for family, count in result.top_families[:3])
        if with_exec:
            exec_status: str = result.exec_outcome if result.exec_outcome else "-"
            detail: str = result.exec_detail if result.exec_detail else "-"
            lines.append(f"| {result.slug} | {status} | {result.fences} | {exec_status} | {detail} | {features} |")
        else:
            lines.append(f"| {result.slug} | {status} | {result.fences} | {features} |")
    return "\n".join(lines)


def render_report(results: list[TreeResult], with_exec: bool = False) -> str:
    """The whole markdown report: summary, table, and the family ranking."""

    total: int = len(results)
    passed: int = sum(1 for result in results if result.compile_pass)
    fences: int = sum(result.fences for result in results)
    ranking: list[tuple[str, int]] = family_ranking(results)
    lines = [
        "# Tree corpus report (R2)",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} by the tree runner.",
        "",
        "Command: `uv run python -m benchmarks.scripts_corpus.trees "
        "--markdown benchmarks/scripts_corpus/REPORT_R2.md`.",
        "",
        f"Rung R2 of the adoption ladder: {passed} of {total} multi-module trees compile with zero fences "
        f"({100 * passed // max(total, 1)}%). The corpus holds {fences} fences in total.",
        "",
    ]
    if with_exec:
        run_ok: int = sum(1 for result in results if result.exec_outcome == OUTCOME_PASS)
        run_wrong: int = sum(1 for result in results if result.exec_outcome == OUTCOME_WRONG_OUTPUT)
        run_built: int = sum(1 for result in results if result.exec_outcome == OUTCOME_BUILD_ERROR)
        lines += [
            f"With binaries built and run against the CPython recordings: {run_ok} match, "
            f"{run_wrong} differ, {run_built} fail to build.",
            "",
        ]
    lines += [
        "## Blocking features",
        "",
        "How many trees each feature family holds back. The top row is the next rung for the compiler.",
        "",
        "| family | trees blocked |",
        "| --- | --- |",
    ]
    for family, count in ranking:
        lines.append(f"| {family} | {count} |")
    if not ranking:
        lines.append("| none | 0 |")
    lines += [
        "",
        "## Trees",
        "",
        render_table(results, with_exec),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts_corpus.trees", description=__doc__.splitlines()[0])
    parser.add_argument("--trees", type=Path, default=TREES, help="the corpus directory")
    parser.add_argument("--only", help="comma separated slugs to analyse")
    parser.add_argument("--markdown", type=Path, help="write the markdown report to this path")
    parser.add_argument("--json", type=Path, help="write the JSON results to this path")
    parser.add_argument(
        "--exec",
        action="store_true",
        help="also build and run every tree that compiles, and compare with the CPython recording",
    )
    parser.add_argument("--quiet", action="store_true", help="print nothing, only write the outputs")
    args = parser.parse_args(argv)
    only: list[str] | None = None
    if args.only:
        only = [item.strip() for item in args.only.split(",") if item.strip()]
    results: list[TreeResult] = analyse_all(args.trees, only)
    if args.exec:
        if shutil.which("clang") is None and shutil.which("zig") is None:
            parser.error("--exec needs a C driver (clang or zig) on PATH")
        for result in results:
            exec_tree(result, recorded_invocation(result.slug), ORACLE)
    if not args.quiet:
        print(render_table(results, args.exec))
    if args.markdown:
        args.markdown.write_text(render_report(results, args.exec), encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "generated": datetime.now(UTC).date().isoformat(),
                    "trees": [result.to_json() for result in results],
                    "ranking": [{"family": family, "treesBlocked": count} for family, count in family_ranking(results)],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
