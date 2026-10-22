"""Cross-check predicted flips against achieved flips.

E1 boundary tool. Reads two JSON inputs:

  * predicted flips: a JSON list of detector records
    (``.agents/docs/spec/e1-entrypoint-detector-spec.md``), keyed by ``path``. A record
    predicts a flip when ``is_script`` is true (a record with an ``error``
    field, or ``is_script: false``, predicts nothing). If the permanent E1
    detector has not landed, these records are synthesized by hand from the
    spec's scan tables; the fixture set under ``tests/fixtures/flipcheck/``
    is such a synthesized example.
  * achieved flips: parsed irscore output, either the ``--out`` baseline JSON
    or the stdout text with ``ADDED``/``REMOVED``/``CHANGED`` sections
    (``benchmarks/compat2/irscore.py``). A probe id counts as achieved when it
    appears in ADDED or CHANGED. REMOVED is ignored: a probe leaving the set
    is not a flip.

Paths in the predicted records identify corpus scripts, so path to probe id is
one rule: ``<script dir>/main.py`` under ``benchmarks/compat2/corpus/scripts/``
becomes ``scripts.<dir>``. Probe ids elsewhere in the corpus are left alone.

Output is stable JSON on stdout: sorted keys, trailing newline, byte-identical
across runs. Exit code 0 unless an input is malformed (exit 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_PREFIX = "benchmarks/compat2/corpus/scripts/"

SECTIONS = ("ADDED", "REMOVED", "CHANGED")
FLIP_SECTIONS = ("ADDED", "CHANGED")


def path_to_probe_id(path: str) -> str:
    """Map a predicted record's path to a corpus probe id.

    ``benchmarks/compat2/corpus/scripts/<dir>/main.py`` -> ``scripts.<dir>``.
    Everything else is returned unchanged.
    """
    prefix = SCRIPTS_PREFIX
    if path.startswith(prefix) and path.endswith("/main.py"):
        rest = path[len(prefix) : -len("/main.py")]
        if rest and "/" not in rest:
            return f"scripts.{rest}"
    return path


def load_predicted(data: Any) -> list[str]:
    """Return the paths a predicted-flip list claims will flip, sorted, deduped."""
    if not isinstance(data, list):
        raise ValueError("predicted input must be a JSON list of records")
    paths: list[str] = []
    for record in data:
        if not isinstance(record, dict):
            raise ValueError("predicted input must be a JSON list of records")
        if "error" in record or not record.get("is_script"):
            continue
        path = record.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("predicted record needs a string 'path' field")
        paths.append(path)
    return sorted(set(paths))


def load_achieved(data: Any) -> list[str]:
    """Return probe ids that flipped, from an irscore --out doc or a diff dict."""
    if not isinstance(data, dict):
        raise ValueError("achieved input must be a JSON object")
    if isinstance(data.get("probes"), dict):
        return sorted(data["probes"])
    raise ValueError("achieved input has neither a 'probes' object nor an ADDED/CHANGED section")


def load_achieved_text(text: str) -> list[str]:
    """Parse irscore stdout (ADDED/REMOVED/CHANGED sections) for flipped ids."""
    ids: set[str] = set()
    section = None
    for line in text.splitlines():
        title = line.strip()
        if title in SECTIONS:
            section = title
            continue
        if section is None or not line.startswith("  "):
            section = None
            continue
        if section in FLIP_SECTIONS and not title.startswith("..."):
            # CHANGED lines are "<probe_id>: <old> -> <new>"; the id is before the colon.
            ids.add(title.split(":", 1)[0].strip())
    return sorted(ids)


def cross_check(predicted: list[str], achieved: list[str]) -> dict[str, Any]:
    """Set-compare mapped predicted ids against achieved ids into a report dict."""
    hits = sorted(set(predicted) & set(achieved))
    misses = sorted(set(predicted) - set(achieved))
    surprises = sorted(set(achieved) - set(predicted))
    predicted_n = len(predicted)
    return {
        "achieved": len(achieved),
        "hit_rate": round(len(hits) / predicted_n, 4) if predicted_n else 0.0,
        "hits": hits,
        "misses": misses,
        "predicted": predicted_n,
        "surprises": surprises,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check predicted flips (detector records) against achieved flips (irscore output).",
    )
    parser.add_argument("predicted", help="predicted-flip JSON file (list of detector records)")
    parser.add_argument("achieved", help="achieved-flip JSON file (irscore --out doc) or stdout text")
    args = parser.parse_args(argv)
    try:
        predicted_text = Path(args.predicted).read_text(encoding="utf-8")
        achieved_text = Path(args.achieved).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read input: {exc}", file=sys.stderr)
        return 2
    try:
        predicted = load_predicted(json.loads(predicted_text))
    except ValueError as exc:
        print(f"bad predicted file {args.predicted}: {exc}", file=sys.stderr)
        return 2
    try:
        achieved = load_achieved(json.loads(achieved_text))
    except ValueError:
        try:
            achieved = load_achieved_text(achieved_text)
        except ValueError as exc:
            print(f"bad achieved file {args.achieved}: {exc}", file=sys.stderr)
            return 2

    predicted_ids = [path_to_probe_id(path) for path in predicted]
    report = cross_check(predicted_ids, achieved)
    print(json.dumps(report, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
