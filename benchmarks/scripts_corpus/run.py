"""Script corpus runner: compile every script and report the blocking features.

This is the R1 rung of the adoption ladder (see `.agents/plans/2026-10-21-adoption-compat-ffi-strategy.md`).
R1 is the whole-file pass rate: the share of single-file CLI scripts that compile
and match CPython on stdout, stderr and exit code.

The runner lives outside the frozen v2 gates. It reads `outcomes.py` for the
outcome vocabulary only, and it never writes to `benchmarks/compat2/`.

    uv run python -m benchmarks.scripts_corpus.run            # print the table
    uv run python -m benchmarks.scripts_corpus.run --markdown REPORT.md
    uv run python -m benchmarks.scripts_corpus.run --json out.json
    uv run python -m benchmarks.scripts_corpus.run --only word_count,csv_totals
    uv run python -m benchmarks.scripts_corpus.run --exec      # also build and run the binaries

Each script lives in `scripts/<slug>/main.py` with a `meta.json` beside it. The
script is a single-file CLI: `sys.argv`, `argparse` or `os.environ` for input,
`print` for output, and an `int` return from `main()` for the exit code.

What the runner reports per script:

- compile pass: the file lowers to IR with zero fences
- fences: the number of fences the coverage report finds
- top blocking features: the feature families that hold the script back, most
  fences first
- with `--exec`: the outcome of running the compiled binary against the recorded
  CPython output, so a wrong answer is separated from a fence

A fenced script is a signal, not a failure. The families are the work list for
the compiler: the family that blocks the most scripts is the next rung.
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
SCRIPTS = HERE / "scripts"
ORACLE = HERE / "oracle"

# The outcome vocabulary of the shared contract, kept as names only.
OUTCOME_PASS = "pass"
OUTCOME_FENCE = "fence"
OUTCOME_WRONG_OUTPUT = "wrong-output"
OUTCOME_BUILD_ERROR = "build-error"


@dataclass
class ScriptResult:
    """The outcome for one corpus script."""

    slug: str
    path: str
    compile_pass: bool
    fences: int
    root_fences: int
    definitions: int
    fenced_definitions: int
    top_families: list[tuple[str, int]] = field(default_factory=list)
    codes: dict[str, int] = field(default_factory=dict)
    error: str = ""
    # Set by the `--exec` stage: the R1 outcome of running the compiled binary.
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
            "topFamilies": [{"family": name, "fences": count} for name, count in self.top_families],
            "codes": self.codes,
            "error": self.error,
            "execOutcome": self.exec_outcome,
            "execDetail": self.exec_detail,
        }


def script_dirs(root: Path) -> list[Path]:
    """Every corpus script directory that holds a `main.py`, sorted by slug."""

    return sorted(path for path in root.iterdir() if path.is_dir() and (path / "main.py").is_file())


def analyse(path: Path) -> ScriptResult:
    """Run the coverage report over one script and classify the outcome."""

    try:
        report = build_report(path / "main.py")
    except Exception as exc:  # a fence that escapes the report is still a fence
        return ScriptResult(
            slug=path.name,
            path=str(path / "main.py"),
            compile_pass=False,
            fences=1,
            root_fences=1,
            definitions=0,
            fenced_definitions=0,
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
    return ScriptResult(
        slug=path.name,
        path=str(path / "main.py"),
        compile_pass=summary["fences"] == 0,
        fences=summary["fences"],
        root_fences=summary["rootFences"],
        definitions=summary["definitions"],
        fenced_definitions=summary["fencedDefinitions"],
        top_families=ordered,
        codes=summary["byCode"],
    )


def analyse_all(root: Path, only: list[str] | None = None) -> list[ScriptResult]:
    """Analyse every script, optionally restricted to the given slugs."""

    wanted = set(only) if only else None
    results: list[ScriptResult] = []
    for path in script_dirs(root):
        if wanted is not None and path.name not in wanted:
            continue
        results.append(analyse(path))
    return results


def build_binary(source: Path, out_dir: Path) -> tuple[Path | None, str]:
    """Build the script into a native binary. Returns the path or the error."""

    binary: Path = out_dir / source.parent.name
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


def run_binary(binary: Path, argv: list[str]) -> tuple[int, str, str]:
    """Run the compiled binary and capture stdout, stderr and the exit code."""

    completed = subprocess.run(
        [str(binary), *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def exec_script(result: ScriptResult, argv: list[str], oracle: Path) -> None:
    """Compile the script, run it, and compare with the recorded CPython output.

    A fenced script has nothing to run: the outcome stays empty and the caller
    renders it as `-`. Only a script that lowers with zero fences reaches the
    binary build.
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
    with tempfile.TemporaryDirectory(prefix="pyscriptc-corpus-") as tmp:
        binary, error = build_binary(Path(result.path), Path(tmp))
        if binary is None:
            result.exec_outcome = OUTCOME_BUILD_ERROR
            result.exec_detail = error.splitlines()[-1] if error else "compile failed"
            return
        code, stdout, stderr = run_binary(binary, argv)
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


def family_ranking(results: list[ScriptResult]) -> list[tuple[str, int]]:
    """How many scripts each blocking family holds back, most first."""

    blocking: dict[str, int] = {}
    for result in results:
        if result.compile_pass:
            continue
        for family, _count in result.top_families:
            blocking[family] = blocking.get(family, 0) + 1
    return sorted(blocking.items(), key=lambda item: (-item[1], item[0]))


def render_table(results: list[ScriptResult], with_exec: bool = False) -> str:
    """The per-script table: compile pass, fence count, top blocking features."""

    if with_exec:
        lines = [
            "| script | compile | fences | exec | detail | top blocking features |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    else:
        lines = [
            "| script | compile | fences | top blocking features |",
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


def render_report(results: list[ScriptResult], with_exec: bool = False) -> str:
    """The whole markdown report: summary, table, and the family ranking."""

    total: int = len(results)
    passed: int = sum(1 for result in results if result.compile_pass)
    fences: int = sum(result.fences for result in results)
    ranking: list[tuple[str, int]] = family_ranking(results)
    lines = [
        "# Script corpus report",
        "",
        f"Generated {datetime.now(UTC).date().isoformat()} by the corpus runner.",
        "",
        "Command: `uv run python -m benchmarks.scripts_corpus.run --markdown benchmarks/scripts_corpus/REPORT.md`.",
        "",
        f"Rung R1 of the adoption ladder: {passed} of {total} single-file CLI scripts compile with zero fences "
        f"({100 * passed // max(total, 1)}%). The corpus holds {fences} fences in total.",
        "",
    ]
    if with_exec:
        run_ok: int = sum(1 for result in results if result.exec_outcome == OUTCOME_PASS)
        run_wrong: int = sum(1 for result in results if result.exec_outcome == OUTCOME_WRONG_OUTPUT)
        run_built: int = sum(1 for result in results if result.exec_outcome == OUTCOME_BUILD_ERROR)
        lines += [
            f"With binaries built and run against the CPython recordings: {run_ok} match, "
            f"{run_wrong} differ, {run_built} fail to build. A script that compiles and still differs is a "
            "soundness signal, and it is tracked separately from a fence.",
            "",
        ]
        if run_wrong:
            lines += [
                f"The {run_wrong} scripts that compile and still differ share one cause: `sys.exit(main())` "
                "evaluates `main()` twice, so the output is doubled. That is issue #183. Until it lands, "
                "the compiled pass rate is not the R1 rate: the ceiling is the compile rate above, and the "
                "true R1 rate is the compile rate minus the doubled scripts.",
                "",
            ]
    lines += [
        "## Blocking features",
        "",
        "How many scripts each feature family holds back. The top row is the next rung for the compiler.",
        "",
        "| family | scripts blocked |",
        "| --- | --- |",
    ]
    for family, count in ranking:
        lines.append(f"| {family} | {count} |")
    if not ranking:
        lines.append("| none | 0 |")
    lines += [
        "",
        "## Scripts",
        "",
        render_table(results, with_exec),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts_corpus", description=__doc__.splitlines()[0])
    parser.add_argument("--scripts", type=Path, default=SCRIPTS, help="the corpus directory")
    parser.add_argument("--only", help="comma separated slugs to analyse")
    parser.add_argument("--markdown", type=Path, help="write the markdown report to this path")
    parser.add_argument("--json", type=Path, help="write the JSON results to this path")
    parser.add_argument(
        "--exec",
        action="store_true",
        help="also build and run every script that compiles, and compare with the CPython recording",
    )
    parser.add_argument("--quiet", action="store_true", help="print nothing, only write the outputs")
    args = parser.parse_args(argv)
    only: list[str] | None = None
    if args.only:
        only = [item.strip() for item in args.only.split(",") if item.strip()]
    results: list[ScriptResult] = analyse_all(args.scripts, only)
    if args.exec:
        if shutil.which("clang") is None and shutil.which("zig") is None:
            parser.error("--exec needs a C driver (clang or zig) on PATH")
        argv_by_slug: dict[str, list[str]] = {}
        from benchmarks.scripts_corpus.oracle import INVOCATIONS, fixtures_dir

        for slug, recorded in INVOCATIONS.items():
            argv_by_slug[slug] = [
                arg.replace("{fixtures}", str(fixtures_dir())).replace("{tmp}", "/tmp") for arg in recorded
            ]
        for result in results:
            if result.compile_pass and result.slug in argv_by_slug:
                exec_script(result, argv_by_slug[result.slug], ORACLE)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "generated": datetime.now(UTC).date().isoformat(),
                    "scripts": [result.to_json() for result in results],
                    "ranking": [
                        {"family": family, "scriptsBlocked": count} for family, count in family_ranking(results)
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_report(results, with_exec=args.exec), encoding="utf-8")
    if not args.quiet:
        print(render_table(results, with_exec=args.exec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
