"""Report and inspection subcommands: coverage, imports, config, reduce, explain, logs.

Split out of `cli.py` by subcommand family (module-size plan, slice 2). Each
command still resolves the compiler modules through `cli._load_compiler`, so
startup cost and behavior are unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyscriptc.config import entry_points, load_config
from pyscriptc.log import get_logger, tail

if TYPE_CHECKING:
    import argparse

log = get_logger()


def _cli() -> Any:
    from pyscriptc import cli

    return cli


def coverage_cmd(args: argparse.Namespace) -> int:
    _cli()._load_compiler()
    report = _cli().build_report(Path(args.source))
    log.info("coverage.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else _cli().render_text(report))
    summary = report["summary"]
    _cli().telemetry.track("coverage.reported", _cli().telemetry.coverage_properties(summary))
    mode = args.fail_on
    if mode is None:
        config = load_config(Path(args.source))
        mode = config.fail_on
    if mode == "any" and summary["fences"]:
        return 1
    if mode == "blockers" and summary["rootFences"]:
        return 1
    return 0


def imports_cmd(args: argparse.Namespace) -> int:
    _cli()._load_compiler()
    source = Path(args.source)
    config = load_config(source)
    report = _cli().build_import_report(source, policy=config.imports)
    log.info("imports.report", source=args.source)
    print(json.dumps(report, indent=2) if args.json else _cli().render_import_text(report))
    if args.reach:
        payload = _cli().export_reach(report, Path(args.reach))
        share = payload["boxed_only_reach"][-1]
        log.info("imports.reach", path=args.reach, reach=share)
        print(f"island reach {share:.4f} appended to {args.reach}", file=sys.stderr)
    return 0


def config_cmd(args: argparse.Namespace) -> int:
    config = load_config(Path(args.source))
    scripts = _scripts_of(Path(args.source))
    data = {
        "source": config.source,
        "origin": str(config.origin) if config.origin else None,
        "entry": config.entry,
        "src": list(config.src),
        "checker": config.checker,
        "driver": config.driver,
        "target": config.target,
        "fail-on": config.fail_on,
        "requires-python": config.requires_python,
        "dependencies": list(config.dependencies),
        "stub-path": list(config.stub_path),
        "ffi": list(config.ffi),
        "dependency-lanes": config.dependency_lanes(),
        "imports": {name: config.lane_for(name)[0] for name in sorted(config.imports)},
        "scripts": scripts,
    }
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print(f"source: {data['source']}")
    if data["origin"]:
        print(f"origin: {data['origin']}")
    for key in ("entry", "checker", "driver", "target", "fail-on", "requires-python"):
        if data[key] is not None:
            print(f"{key}: {data[key]}")
    if data["src"]:
        print(f"src: {', '.join(data['src'])}")
    for key in ("stub-path", "ffi"):
        if data[key]:
            print(f"{key}: {', '.join(data[key])}")
    if data["dependencies"]:
        print(f"dependencies: {', '.join(data['dependencies'])}")
        for req, lane in data["dependency-lanes"].items():
            print(f"  {req}: {lane}")
    for name, lane in data["imports"].items():
        print(f"import {name}: {lane}")
    if data["scripts"]:
        print("scripts:")
        for name, target in data["scripts"].items():
            print(f"  {name}: {target}")
    return 0


def _scripts_of(source: Path) -> dict[str, str]:
    """The console scripts of the pyproject above `source`, if any."""

    from pyscriptc import project

    try:
        pyproject = project.discover_pyproject(source)
    except project.ProjectError:
        return {}
    try:
        return entry_points(pyproject)
    except Exception:
        return {}


def _reduce_predicate(args: argparse.Namespace) -> _cli().reduce.Predicate:
    """The predicate the reducer checks on every candidate."""
    if args.predicate:
        source = Path(args.source)
        return _cli().reduce.CommandPredicate(
            args.predicate,
            directory=source.parent if source.parent.is_dir() else Path.cwd(),
            suffix=source.suffix or ".py",
            timeout=args.timeout,
        )
    if args.fence:
        return _cli().reduce.FencePredicate(args.fence)
    return _cli().reduce.CrashPredicate()


def reduce_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc _cli().reduce FILE`: shrink FILE to a minimal reproducer."""
    _cli()._load_compiler()
    predicate = _reduce_predicate(args)
    out_path = Path(args.source).with_name(f"{Path(args.source).stem}.min.py")
    target = out_path if not args.print and not args.json else None
    result = _cli().reduce.reduce_file(args.source, predicate, output=target, max_steps=args.steps)
    kept = sum(1 for step in result.steps if step.kept)
    log.info(
        "_cli().reduce.done",
        source=args.source,
        candidates=result.candidates,
        kept=kept,
        verdict=str(result.verdict),
    )
    _cli().telemetry.track(
        "_cli().reduce.finished",
        {
            "candidates": result.candidates,
            "steps": len(result.steps),
            "kept": kept,
            "changed": result.changed,
            "mode": "predicate" if args.predicate else "fence" if args.fence else "crash",
        },
    )
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
    elif args.print:
        print(result.source, end="")
    else:
        print(f"predicate: {result.predicate}")
        print(f"{result.candidates} candidates, {len(result.steps)} steps, {kept} kept")
        print(f"wrote {out_path} ({'changed' if result.changed else 'no reduction found'})")
        print(f"verdict: {result.verdict}")
    return 0 if result.verdict.holds else 1


def explain_cmd(args: argparse.Namespace) -> int:
    _cli()._load_compiler()
    text = _cli().explain(args.code)
    log.info("_cli().explain.lookup", code=args.code, known=text is not None)
    if text is None:
        print(f"unknown diagnostic code: {args.code}", file=sys.stderr)
        return 2
    print(text)
    return 0


def logs_cmd(args: argparse.Namespace) -> int:
    tail()
    return 0
