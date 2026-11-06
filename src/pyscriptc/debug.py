"""Compiler debug mode.

`--debug` (or `PYSC_DEBUG`) prints a compact per-stage table to stderr.
`--timings` prints only that table. `--debug-dir DIR` additionally writes the
intermediate artifacts the agent needs to inspect a compile: the lowered
module (`ir.json`), the emitted LLVM text (`out.ll`), the exact cc command
lines (`cc.sh`), and a machine-readable `summary.json` (which always carries
per-stage wall times in `timings`). Debug output never touches the IR or the
emitted text.

"""

from __future__ import annotations

import json
import os
import resource
import time
from pathlib import Path
from typing import Any

from pyscriptc.emit._ir import (
    _has,
    _items,
    _kind_or_none,
    _node_name,
    _opt_int,
    _opt_span,
    _opt_str_or_none,
)

_TOPICS = {"stages", "fences", "cc", "paths", "verify", "mir", "all"}


def parse_topics(value: str | None) -> set[str]:
    """A `--debug`/`PYSC_DEBUG` value: empty means `stages,verify`, else a comma list."""
    if value is None or not value.strip() or value.strip() in ("1", "true", "on"):
        return {"stages", "verify"}
    topics = {topic.strip() for topic in value.split(",") if topic.strip()}
    unknown = topics - _TOPICS
    if unknown:
        raise ValueError(f"unknown debug topic(s): {', '.join(sorted(unknown))}")
    return topics


def resolve(flag: str | None, debug_dir: str | None, timings: bool = False) -> Debug | None:
    """`--debug` wins over `PYSC_DEBUG`; `--debug-dir` alone also enables debug.

    `timings` asks for the per-stage wall-time table on its own.
    """
    value = flag if flag is not None else os.environ.get("PYSC_DEBUG")
    if flag is None and not value and debug_dir is None and not timings:
        return None
    topics = parse_topics(value) if (flag is not None or value) else set()
    if debug_dir is not None:
        # The dump is the point of `--debug-dir`: it carries the verification
        # verdicts, so the directory always turns the verifier on.
        topics.add("paths")
        topics.add("verify")
    if timings:
        topics.add("stages")
    return Debug(topics, None if debug_dir is None else Path(debug_dir))


class Debug:
    # The B11 MIR adapter record, set by the CLI only on the MIR lane:
    # lowered/adapter counts plus the per-function fallback records. The
    # bare annotation registers the field; `self.x: T = v` AnnAssigns do
    # not lower, so `__init__` assigns a plain `None`.
    mir_plan: dict[str, Any] | None

    def __init__(self, topics: set[str], debug_dir: Path | None) -> None:
        self.topics = topics
        self.debug_dir = debug_dir
        # (name, milliseconds, primary): primary entries are the compile
        # stages, the others are the finer measurements inside a stage.
        self.entries: list[tuple[str, float, bool]] = []
        self.commands: list[list[str]] = []
        self._starts: list[float] = []
        # Per-stage metrics, recorded by `end` when the stage hands over the
        # module: wall ms (also in `entries`), ru_maxrss delta since the
        # previous peak, IR node count and function count.
        self.stage_stats: dict[str, dict[str, Any]] = {}
        self._rss_peak = _rss_kb()
        self._t0 = time.perf_counter()
        self.timings_map: dict[str, float] = _TimingSink(self)
        # Verification results, recorded by the compile pipeline when the
        # `verify` topic is on: the IR structural pass and the LLVM module
        # verifier over the emitted text.
        self.ir_verifications: list[dict[str, Any]] = []
        self.llvm_verification: dict[str, Any] | None = None
        self.binding_info: dict[str, Any] | None = None
        self.mir_plan = None

    def set_binding(self, emitted: list[dict[str, Any]], skipped: list[tuple[str, str]]) -> None:
        self.binding_info = {
            "emitted": [_node_name(fn) for fn in emitted],
            "emittedCount": len(emitted),
            "skipped": [{"name": name, "reason": reason} for name, reason in skipped],
            "skippedCount": len(skipped),
        }

    def set_mir(self, lowered: int, records: list[dict[str, Any]]) -> None:
        # Records print sorted by module then function. `sorted(key=)`
        # does not lower on record elements, so the sort keys are plain
        # strings looked back up in a name map.
        keys: list[str] = []
        record_by_key: dict[str, dict[str, Any]] = {}
        for record in records:
            record_module: str = record["module"]
            record_fn: str = record["function"]
            key = record_module + "\x00" + record_fn
            keys.append(key)
            record_by_key[key] = record
        ordered: list[dict[str, Any]] = []
        for key in sorted(keys):
            rec: dict[str, Any] = record_by_key[key]
            ordered.append(rec)
        plan: dict[str, Any] = {}
        plan["lowered"] = lowered
        plan["adapter"] = len(ordered)
        plan["records"] = ordered
        self.mir_plan = plan

    def wants(self, topic: str) -> bool:
        return "all" in self.topics or topic in self.topics

    def begin(self, name: str) -> None:
        self._starts.append(time.perf_counter())

    def end(self, name: str, module: dict[str, Any] | None = None) -> None:
        ms = (time.perf_counter() - self._starts.pop()) * 1000.0
        self.entries.append((name, ms, True))
        rss = _rss_kb()
        self.stage_stats[name] = {
            "ms": round(ms, 3),
            "rssKb": max(rss - self._rss_peak, 0),
            "nodes": _count_nodes(module),
            "functions": 0 if module is None else len(_items(module, "functions")),
        }
        self._rss_peak = max(self._rss_peak, rss)

    def add(self, name: str, ms: float) -> None:
        """Record a measurement taken inside a stage (parse, cc, link)."""
        self.entries.append((name, ms, False))

    def exclude(self, stage: str, inner: str) -> None:
        """Take `inner` out of `stage`, so the two do not double count.

        `lower` covers the whole lowering call; `parse` is the part of it that
        went into the Python parser. The stage keeps the remainder.
        """
        inner_ms = next((ms for name, ms, primary in self.entries if name == inner and not primary), None)
        if inner_ms is None:
            return
        for index, (name, ms, primary) in enumerate(self.entries):
            if name == stage and primary:
                self.entries[index] = (name, max(ms - inner_ms, 0.0), primary)
                return

    @property
    def stages(self) -> list[tuple[str, float]]:
        """The primary stages, in order."""
        return [(name, ms) for name, ms, primary in self.entries if primary]

    def timings(self) -> dict[str, float]:
        """Stage name to wall milliseconds, in the order the stages ran."""
        return {name: round(ms, 3) for name, ms, _ in self.entries}

    def render(self, module: dict[str, Any] | None, outputs: dict[str, str]) -> str:
        lines: list[str] = []
        mir_plan = self.mir_plan
        if self.wants("stages"):
            lines.append("[debug] stage           ms    rssKB   nodes   funcs")
            for name, ms, primary in self.entries:
                stats = self.stage_stats.get(name) if primary else None
                if stats is None:
                    lines.append(f"[debug] {name:<12}{ms:>8.1f}")
                else:
                    funcs = _opt_int(stats, "functions", 0)
                    lines.append(f"[debug] {name:<12}{ms:>8.1f}{stats['rssKb']:>8}{stats['nodes']:>8}{funcs:>8}")
                if name == "emit_llvm" and mir_plan is not None:
                    mir_lowered: int = mir_plan["lowered"]
                    mir_adapter: int = mir_plan["adapter"]
                    lines.append(f"[debug] mir  lowered {mir_lowered}  adapter {mir_adapter}")
            total = (time.perf_counter() - self._t0) * 1000.0
            lines.append(f"[debug] {'total':<12}{total:>8.1f}")
        if mir_plan is not None and self.wants("mir"):
            records: list[dict[str, Any]] = mir_plan["records"]
            for record in records:
                record_module: str = record["module"]
                record_fn: str = record["function"]
                record_reason: str = record["reason"]
                lines.append(f"[debug] mir.adapter {record_module}:{record_fn}  {record_reason}")
        if module is not None and self.wants("stages"):
            lines.extend(_capability_lines(module))
        if module is not None and self.wants("fences"):
            fences = _items(module, "fences")
            lines.append(f"[debug] fences: {len(fences)}")
            for fence in fences:
                span = _opt_span(fence) or {}
                lines.append(
                    f"[debug]   {_opt_str_or_none(span, 'file')}:{span.get('line')}:{span.get('col')}"
                    f" {fence.get('code')} {fence.get('message')}"
                )
        if self.commands and self.wants("cc"):
            lines.append("[debug] cc:")
            for argv in self.commands:
                lines.append(f"[debug]   {' '.join(argv)}")
        if outputs and self.wants("paths"):
            for key, value in outputs.items():
                lines.append(f"[debug] {key}: {value}")
        if self.wants("verify"):
            for entry in self.ir_verifications:
                lines.append(f"[debug] irverify: {entry}")
            if self.llvm_verification is not None:
                result = self.llvm_verification
                if result.get("ok"):
                    lines.append(f"[debug] llvmverify: ok ({result.get('tool')})")
                else:
                    lines.append(f"[debug] llvmverify: {result.get('tool')}: {result.get('message')}")
        if self.binding_info is not None and self.wants("stages"):
            lines.append(
                f"[debug] binding: {self.binding_info['emittedCount']} exported, "
                f"{self.binding_info['skippedCount']} skipped"
            )
        return "\n".join(lines)

    def summary(self, module: dict[str, Any] | None, outputs: dict[str, str], status: str) -> dict[str, Any]:
        res: dict[str, Any] = {
            "stages": [{"name": name, "ms": round(ms, 3)} for name, ms in self.stages],
            "stageStats": [
                {"name": name, **self.stage_stats[name]}
                for name, _, primary in self.entries
                if primary and name in self.stage_stats
            ],
            "timings": self.timings(),
            "totalMs": round((time.perf_counter() - self._t0) * 1000.0, 3),
            "fences": [] if module is None else _items(module, "fences"),
            "functions": 0 if module is None else len(_items(module, "functions")),
            "capabilities": {} if module is None else _capability_report(module),
            "commands": self.commands,
            "outputs": outputs,
            "status": status,
            "verify": {
                "ir": self.ir_verifications,
                "llvm": self.llvm_verification,
            },
        }
        if self.binding_info is not None:
            res["binding"] = self.binding_info
            if self.binding_info.get("skippedCount", 0) > 0:
                res["bindingSkipped"] = self.binding_info["skippedCount"]
        mir_plan = self.mir_plan
        if mir_plan is not None:
            mir_records: list[dict[str, Any]] = mir_plan["records"]
            res["mirAdapter"] = mir_records
        # boxed:begin
        if module is not None and _has(module, "fallback"):
            res["fallback"] = module["fallback"]
        # boxed:end
        return res

    def write_dir(
        self,
        module: dict[str, Any] | None,
        ll_text: str | None,
        outputs: dict[str, str],
        status: str,
    ) -> None:
        if self.debug_dir is None:
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        if module is not None:
            (self.debug_dir / "ir.json").write_text(json.dumps(module, indent=2) + "\n", encoding="utf-8")
        if ll_text is not None:
            (self.debug_dir / "out.ll").write_text(ll_text, encoding="utf-8")
        if self.commands:
            script = "#!/bin/sh\n# exact cc invocations pyscriptc ran\n" + "\n".join(
                " ".join(argv) for argv in self.commands
            )
            (self.debug_dir / "cc.sh").write_text(script + "\n", encoding="utf-8")
        (self.debug_dir / "summary.json").write_text(
            json.dumps(self.summary(module, outputs, status), indent=2) + "\n", encoding="utf-8"
        )


def _rss_kb() -> int:
    """Peak resident set size of this process so far, in KiB."""

    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except OSError, ValueError:
        return 0


def _count_nodes(module: dict[str, Any] | None) -> int:
    """Every IR node in the module: a dict with a string `kind` key."""

    if module is None:
        return 0

    def walk(value: Any) -> int:
        if isinstance(value, dict):
            count = 1 if isinstance(_kind_or_none(value), str) else 0
            return count + sum(walk(item) for item in value.values())
        if isinstance(value, list):
            return sum(walk(item) for item in value)
        return 0

    return walk(module)


def _module_files(module: dict[str, Any]) -> list[str]:
    """Every source file the lowered module mentions."""

    files: set[str] = set()
    for fence in _items(module, "fences"):
        span = _opt_span(fence) or {}
        path = _opt_str_or_none(span, "file")
        if path:
            files.add(str(path))
    for key in ("functions", "classes"):
        for item in _items(module, key):
            span = _opt_span(item) or {}
            path = _opt_str_or_none(span, "file")
            if path:
                files.add(str(path))
    return sorted(files)


def _capability_report(module: dict[str, Any]) -> dict[str, Any]:
    """The per-file native, fenced and island split the audit surface prints."""

    from pyscriptc.coverage import capabilities

    return capabilities(module, _module_files(module))


def _capability_lines(module: dict[str, Any]) -> list[str]:
    report = _capability_report(module)
    totals = report["totals"]
    lines = [
        f"[debug] capability: {totals['native']} native, {totals['fenced']} fenced, "
        f"{totals['island']} island definitions of {totals['definitions']}"
    ]
    for item in report["byFile"]:
        if not (item["fences"] or item["definitions"]):
            continue
        mechanisms = f", reflection: {', '.join(item['mechanisms'])}" if item["mechanisms"] else ""
        lines.append(
            f"[debug]   {item['file']}: {item['native']} native, {item['fenced']} fenced, "
            f"{item['island']} island of {item['definitions']} definitions, {item['fences']} fences{mechanisms}"
        )
    return lines


class _TimingSink(dict):
    """A dict-like sink that forwards writes into `Debug.entries`.

    Passed to `lower_source`, `link_binary` and `compile_unit`, which only
    need a mutable mapping; each written key lands as a non-primary entry.
    """

    def __init__(self, dbg: Debug) -> None:
        super().__init__()
        self._dbg = dbg

    def __setitem__(self, name: str, ms: float) -> None:
        self._dbg.add(name, ms)
