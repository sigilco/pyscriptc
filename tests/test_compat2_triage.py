"""The v1-lowered fence triage runs on the frozen files and leaves no row unclassified."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from benchmarks.compat2.probes.triage import FENCE_BUCKETS, build_report, problems, render

PROBES = Path(__file__).resolve().parents[1] / "benchmarks" / "compat2" / "probes"

# Locked to the frozen calibration. A predicate change that moves a row fails here.
RULE_COUNTS = {
    "raises-fallback": 9,
    "private-annotation": 9,
    "seq-int-remainder": 1,
    "int-operand": 3,
    "bisect-str-haystack": 2,
    "sum-bools": 1,
    "listdir-elem": 1,
    "parse-qs-elem": 1,
    "struct-format-parameter": 2,
    "empty-tuple-annotation": 1,
    "rmtree-unchecked": 1,
    "posixpath-concrete-class": 1,
    "enum-constant": 139,
    "zero-arg-constructor": 7,
    "iterator-object": 3,
    "container-class": 2,
    "split-tuple": 2,
    "result-class": 2,
    "strftime-format-only": 1,
    "mirrored-int": 1,
}

EXAMPLES = {
    "stdlib.ctypes.sizeof": "raises-fallback",
    "stdlib.sys.exit": "raises-fallback",
    "builtins.open": "private-annotation",
    "builtins.sorted": "seq-int-remainder",
    "builtins.getattr": "int-operand",
    "stdlib.bisect.bisect_left": "bisect-str-haystack",
    "builtins.sum": "sum-bools",
    "stdlib.os.listdir": "listdir-elem",
    "stdlib.urllib.parse.parse_qs": "parse-qs-elem",
    "stdlib.struct.pack": "struct-format-parameter",
    "builtins.tuple": "empty-tuple-annotation",
    "stdlib.shutil.rmtree": "rmtree-unchecked",
    "stdlib.pathlib.Path": "posixpath-concrete-class",
    "stdlib.socket.AF_INET": "enum-constant",
    "builtins.bool": "zero-arg-constructor",
    "stdlib.math.hypot": "zero-arg-constructor",
    "builtins.range": "iterator-object",
    "stdlib.collections.deque": "container-class",
    "stdlib.posixpath.split": "split-tuple",
    "stdlib.re.compile": "result-class",
    "stdlib.time.strftime": "strftime-format-only",
    "stdlib.unicodedata.mirrored": "mirrored-int",
}


def test_frozen_fence_rows_are_classified() -> None:
    report = build_report()
    assert problems(report) == []
    assert report.not_passing == 190
    assert report.fence_expected == 189
    assert len(report.fences) == 189
    assert report.unclassified == ()
    assert [item.id for item in report.out_of_scope] == ["stdlib.os.getenv"]
    assert report.out_of_scope[0].cell == "surface/function/value"
    assert report.out_of_scope[0].rule == "out-of-scope-getenv"
    buckets = Counter(item.bucket for item in report.fences)
    assert buckets["probe wrong"] == 32
    assert buckets["stricter than v1"] == 139
    assert buckets["compiler gap"] == 18
    assert sum(buckets[bucket] for bucket in FENCE_BUCKETS) == 189
    assert Counter(item.rule for item in report.fences) == RULE_COUNTS
    by_id = {item.id: item.rule for item in report.fences}
    assert "stdlib.os.getenv" not in by_id
    for probe_id, rule in EXAMPLES.items():
        assert by_id[probe_id] == rule
    text = (PROBES / "TRIAGE.md").read_text(encoding="utf-8")
    assert text == render(report)
    listed = [line for line in text.splitlines() if line.startswith("| `builtins.") or line.startswith("| `stdlib.")]
    assert len(listed) == 189
    assert "unclassified | 0" in text
