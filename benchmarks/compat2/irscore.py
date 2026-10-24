"""Per probe IR hash baseline: generate, and compare against a baseline file.

The canonicalization and hashing live in `pyscriptc.canonical` (contract:
`hash_module(module_ir) -> 64 hex lowercase`, `CANONICAL_SCHEMA_VERSION` = the
canonical-form version the hashes are valid under). This module only drives
lowering per probe and owns the baseline file format:

    {"version": 1, "revision": 1, "canonical_schema": "canonical-schema/1",
     "oracle": "<oracle_version()>", "hash_algo": "sha256",
     "probes": {"<probe_id>": "<64-hex>"}}

The header carries the schema and oracle version once, so a regenerated baseline
diffs as one header line plus only the probes that actually moved. Serialization
is `json.dumps(..., sort_keys=True, indent=1)` plus a trailing newline: stable
bytes, reviewable diffs.

Exit codes: 0 clean (or generated), 1 any hash diff, 2 header mismatch (stale
baseline: regenerate, do not hand edit).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from benchmarks.compat2.cache import oracle_version
from benchmarks.compat2.loader import load_probes

BASELINE_VERSION = 1
BASELINE_REVISION = 1
HASH_ALGO = "sha256"
# Reviewability cap: sections longer than this collapse to a count.
DIFF_CAP = 20

_REGENERATE_HINT = "the baseline is stale for this compiler build: regenerate it (irscore <probes> --out FILE)"


def _lower_one(probe, src: Path, *, bridge: str | None = None) -> dict[str, Any]:
    """Lower one probe's source to a module dict. Same path as coverage_map.trace_probe."""

    from pyscriptc.lower import lower_file

    src.write_text(probe.source, encoding="utf-8")
    checker_bridge = None
    if bridge is not None:
        from pyscriptc.factsbridge import CheckerBridge

        checker_bridge = CheckerBridge.for_path(src, bridge)
    module, _ = lower_file(str(src), bridge=checker_bridge)
    module.pop("_notes", None)
    return module


def probe_hashes(probes: list, *, workers: int = 1, bridge: str | None = None) -> dict[str, str]:
    """Lower every probe and map probe id to the canonical IR hash, in probe order."""

    from pyscriptc.canonical import hash_module

    hashes: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="compat2-irs-") as tmp:
        for index, probe in enumerate(probes):
            hashes[probe.id] = hash_module(_lower_one(probe, Path(tmp) / f"p{index}.py", bridge=bridge))
    return hashes


def build_doc(hashes: dict[str, str], bridge: str | None = None) -> dict[str, Any]:
    from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION

    doc: dict[str, Any] = {
        "version": BASELINE_VERSION,
        "revision": BASELINE_REVISION,
        "canonical_schema": CANONICAL_SCHEMA_VERSION,
        "oracle": oracle_version(),
        "hash_algo": HASH_ALGO,
        "probes": hashes,
    }
    # A bridged run's IR depends on the checker version too, so the record
    # names it next to the oracle: a `bridge` field with no `bridge_version`
    # would not reproduce (design decision `.agents/docs/design/checker-bridge.md`).
    if bridge is not None:
        from pyscriptc import factsbridge

        provider_version = factsbridge.provider_version(bridge)
        if provider_version is None:
            raise BaselineError(f"the {bridge} bridge is not available in this interpreter")
        doc["bridge"] = bridge
        doc["bridge_version"] = provider_version
    return doc


def serialize(doc: dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def write_baseline(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize(doc), encoding="utf-8")


class BaselineError(Exception):
    """The baseline file cannot be compared: bad shape or stale header. Exit 2."""


def load_baseline(path: Path) -> dict[str, Any]:
    """Parse a baseline file, or raise BaselineError (exit 2)."""

    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc
    except ValueError as exc:
        raise BaselineError(f"baseline {path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("probes"), dict):
        raise BaselineError(f"baseline {path} has no probes object: {_REGENERATE_HINT}")
    return doc


def check_header(doc: dict[str, Any]) -> None:
    """Raise BaselineError when the baseline was made under a different schema, oracle or bridge (stale)."""

    from pyscriptc.canonical import CANONICAL_SCHEMA_VERSION

    wrong = []
    if doc.get("canonical_schema") != CANONICAL_SCHEMA_VERSION:
        wrong.append(f"canonical_schema {doc.get('canonical_schema')!r} != current {CANONICAL_SCHEMA_VERSION!r}")
    if doc.get("oracle") != oracle_version():
        wrong.append(f"oracle {doc.get('oracle')!r} != current {oracle_version()!r}")
    if doc.get("bridge") is not None:
        from pyscriptc import factsbridge

        provider = doc["bridge"]
        current = factsbridge.provider_version(provider)
        if current is None or doc.get("bridge_version") != current:
            wrong.append(f"bridge_version {doc.get('bridge_version')!r} != current {current!r} for {provider!r}")
    if wrong:
        raise BaselineError("; ".join(wrong) + f"; {_REGENERATE_HINT}")


def compare(old: dict[str, str], new: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(pid for pid in old.keys() & new.keys() if old[pid] != new[pid])
    return added, removed, changed


def print_diff(old_probes: dict[str, str], new_probes: dict[str, str], out=None) -> None:
    stream = sys.stdout if out is None else out
    added, removed, changed = compare(old_probes, new_probes)
    total = len(added) + len(removed) + len(changed)
    print(f"+{len(added)} added, -{len(removed)} removed, ~{len(changed)} changed ({total} total)", file=stream)
    _section("ADDED", added, None, stream)
    _section("REMOVED", removed, None, stream)
    changed_lines = [f"{pid}: {old_probes[pid][:8]} -> {new_probes[pid][:8]}" for pid in changed]
    _section("CHANGED", changed_lines, None, stream)
    if total == 0:
        print("baseline matches", file=stream)


def _section(title: str, lines: list[str], _unused: dict[str, str] | None, out) -> None:
    if not lines:
        return
    print(f"\n{title}", file=out)
    for line in lines[:DIFF_CAP]:
        print(f"  {line}", file=out)
    if len(lines) > DIFF_CAP:
        print(f"  ... and {len(lines) - DIFF_CAP} more", file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per probe canonical IR hash baseline (generate, or compare against one).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("probes", help="probe JSONL file or directory")
    parser.add_argument("--limit", type=int, help="only the first N probes (debug)")
    parser.add_argument("--out", help="write a fresh baseline here (generate mode)")
    parser.add_argument("--baseline", help="compare against this baseline file")
    parser.add_argument(
        "--bridge",
        help="lower through a checker bridge (e.g. mypy) and record the checker version in the baseline",
    )
    try:
        args = parser.parse_args(argv)
        if not args.out and not args.baseline:
            parser.error("give --out (generate) or --baseline (compare)")
        if args.out and args.baseline:
            parser.error("--out and --baseline are exclusive: generate or compare, not both")

        probes = list(load_probes(args.probes))
        if args.limit is not None:
            probes = probes[: args.limit]
        new_probes = probe_hashes(probes, bridge=args.bridge)

        if args.out:
            write_baseline(Path(args.out), build_doc(new_probes, bridge=args.bridge))
            print(f"wrote {len(new_probes)} probe hashes to {args.out}")
            return 0

        baseline = load_baseline(Path(args.baseline))
        check_header(baseline)
        added, removed, changed = compare(baseline["probes"], new_probes)
        print_diff(baseline["probes"], new_probes)
        return 1 if added or removed or changed else 0
    except BaselineError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
