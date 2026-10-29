"""Report and inspection subcommands: coverage, imports, config, reduce, explain, logs.

Split out of `cli.py` by subcommand family (module-size plan, slice 2). Each
command still resolves the compiler modules through `cli._load_compiler`, so
startup cost and behavior are unchanged.
"""

from __future__ import annotations

import json
import sys
import time
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


def infer_cmd(args: argparse.Namespace) -> int:
    """`pyscriptc infer`: what the checker proves for a package, and what lowering gains.

    The command is a report first: it runs the checker over the tree,
    reduces the call sites to the pins every caller agrees on, lowers
    with the pins and the bridge applied, and prints the per-lever delta
    (native definitions with and without inference). `--overlay` writes
    the accepted annotations as a sidecar; the report never edits the
    source.
    """
    from pyscriptc.coverage import build_report
    from pyscriptc.inferrunner import build_facts

    source = Path(args.source)
    started = time.perf_counter()
    facts = build_facts(source)
    fact_seconds = time.perf_counter() - started

    # Baseline: the package as it lowers today, no inference.
    started = time.perf_counter()
    base_report = build_report(source)
    base_seconds = time.perf_counter() - started
    base_summary = base_report["summary"]

    # With inference: the pins lower through `_checker_pin_type`, the
    # bridge (per file) proves returns and locals the checker types. The
    # bridge facts come from a pinned re-check: the checker's own return
    # inference reads the pinned parameters, so its answer is consistent
    # with what the lowerer will apply.
    from pyscriptc.factsbridge import BridgeProvider, CheckerBridge
    from pyscriptc.inferrunner import facts_as_pins, pinned_facts

    pin_map = facts_as_pins(facts)
    facts_by_file = pinned_facts(source, facts)

    class _ProviderWithPins(BridgeProvider):
        def for_file(self, path: Path):  # noqa: ANN202 - matches the base shape
            found = facts_by_file.get(path.resolve())
            if found is not None:
                bridge = CheckerBridge(found, provider.records)
            else:
                bridge = super().for_file(path)
            bridge.call_site_facts = pin_map
            return bridge

    provider = _ProviderWithPins(args.bridge, strict=False)
    started = time.perf_counter()
    inferred_report = build_report(source, bridge=provider)
    infer_seconds = time.perf_counter() - started
    inferred_summary = inferred_report["summary"]

    # The records the lowerer accepted, gathered through the provider.
    # The catalog passes lower a unit more than once, so the same fact is
    # recorded per pass; dedupe by the record's identity.
    seen_records: set[tuple[Any, ...]] = set()
    records = []
    for record in provider.records:
        key = (record["kind"], record["module"], record["name"], record["type"])
        if key in seen_records:
            continue
        seen_records.add(key)
        records.append(record)
    pins = facts.pins
    summary = {
        "source": str(source),
        "provider": facts.provider,
        "checker_seconds": round(fact_seconds, 2),
        "baseline_seconds": round(base_seconds, 2),
        "inferred_seconds": round(infer_seconds, 2),
        "pins": facts.pin_summary(),
        "native_baseline": base_summary["nativeDefinitions"],
        "native_inferred": inferred_summary["nativeDefinitions"],
        "native_delta": inferred_summary["nativeDefinitions"] - base_summary["nativeDefinitions"],
        "fences_baseline": base_summary["fences"],
        "fences_inferred": inferred_summary["fences"],
        "fences_delta": inferred_summary["fences"] - base_summary["fences"],
        "definitions": base_summary["definitions"],
        "pinned_params": sorted({f"{callee}.{param}" for callee, param in pins}),
        "records": len(records),
        "records_detail": records,
    }
    if args.overlay:
        lines = ["# pyscriptc inferred overlay: checker-proved, package-wide pins."]
        lines.append("# Apply by hand; a pin the checker proved at every call site is sound")
        lines.append("# only while those call sites stay the same.")
        for callee, param in sorted(pins):
            pin = pins[(callee, param)]
            lines.append(f"# {callee}: {param}: {pin.kind} ({pin.sites} call site(s))")
        Path(args.overlay).write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary["overlay"] = args.overlay
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(f"source: {summary['source']}")
    print(f"provider: {summary['provider']} ({summary['checker_seconds']:.2f}s to collect facts)")
    print(f"call sites resolved: {summary['pins']['call_sites']}")
    print(f"parameters pinned: {summary['pins']['pinned_params']}")
    print(
        f"native definitions: {summary['native_baseline']} -> {summary['native_inferred']}"
        f" ({summary['native_delta']:+d}) of {summary['definitions']}"
    )
    print(f"fences: {summary['fences_baseline']} -> {summary['fences_inferred']} ({summary['fences_delta']:+d})")
    if summary["pinned_params"]:
        print("pinned parameters:")
        for item in summary["pinned_params"]:
            print(f"  {item}")
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
