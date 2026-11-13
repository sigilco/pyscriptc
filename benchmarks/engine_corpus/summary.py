#!/usr/bin/env python3
"""Markdown summaries of the conformance and marshalling reports (#670).

The CI job writes its step summary from these, so the formatting lives in
one tested place instead of in a heredoc inside a workflow file.

    uv run python -m benchmarks.engine_corpus.summary conformance REPORT.json
    uv run python -m benchmarks.engine_corpus.summary marshalling REPORT.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def conformance_summary(path: Path) -> str:
    report = json.loads(path.read_text(encoding="utf-8"))
    engines = ", ".join(report.get("engines") or []) or "(none)"
    skipped = ", ".join(report.get("skipped") or []) or "(none)"
    passed = report.get("passed", 0)
    total = report.get("total", 0)
    lines = [
        "### Engine conformance",
        "",
        f"Engines: {engines}",
        f"Skipped: {skipped}",
        f"Programs matching CPython: {passed}/{total}",
        "",
        "| engine | program | result |",
        "| --- | --- | --- |",
    ]
    for row in report.get("programs", []):
        lines.append(f"| {row['engine']} | {row['program']} | {'ok' if row['ok'] else 'DIFF'} |")
    return "\n".join(lines)


def marshalling_summary(path: Path) -> str:
    report = json.loads(path.read_text(encoding="utf-8"))
    calls = report.get("calls", 0)
    repeats = report.get("repeats", 0)
    lines = [
        "### Island marshalling per call",
        "",
        f"{calls} calls x {repeats} repeats, best run, ns/call.",
        "",
        "| case | ns/call | marshalling share |",
        "| --- | --- | --- |",
    ]
    for name, stats in report.get("cases", {}).items():
        share = "baseline" if name == "base" else f"+{stats['marshal_ns']:.0f} ns"
        lines.append(f"| {name} | {stats['ns_per_call']:.1f} | {share} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: summary.py {conformance|marshalling} REPORT.json", file=sys.stderr)
        return 2
    kind, path = argv
    path = Path(path)
    if not path.is_file():
        print(f"{path} does not exist")
        return 0
    if kind == "conformance":
        print(conformance_summary(path))
    elif kind == "marshalling":
        print(marshalling_summary(path))
    else:
        print(f"unknown summary kind {kind!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
