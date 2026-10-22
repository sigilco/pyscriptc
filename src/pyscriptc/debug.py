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
import time
from pathlib import Path
from typing import Any

_TOPICS = {"stages", "fences", "cc", "paths", "verify", "all"}


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
    def __init__(self, topics: set[str], debug_dir: Path | None) -> None:
        self.topics = topics
        self.debug_dir = debug_dir
        # (name, milliseconds, primary): primary entries are the compile
        # stages, the others are the finer measurements inside a stage.
        self.entries: list[tuple[str, float, bool]] = []
        self.commands: list[list[str]] = []
        self._starts: list[float] = []
        self._t0 = time.perf_counter()
        self.timings_map: dict[str, float] = _TimingSink(self)
        # Verification results, recorded by the compile pipeline when the
        # `verify` topic is on: the IR structural pass and the LLVM module
        # verifier over the emitted text.
        self.ir_verifications: list[dict[str, Any]] = []
        self.llvm_verification: dict[str, Any] | None = None

    def wants(self, topic: str) -> bool:
        return "all" in self.topics or topic in self.topics

    def begin(self, name: str) -> None:
        self._starts.append(time.perf_counter())

    def end(self, name: str) -> None:
        self.entries.append((name, (time.perf_counter() - self._starts.pop()) * 1000.0, True))

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
        if self.wants("stages"):
            lines.append("[debug] stage           ms")
            for name, ms, _ in self.entries:
                lines.append(f"[debug] {name:<12}{ms:>8.1f}")
            total = (time.perf_counter() - self._t0) * 1000.0
            lines.append(f"[debug] {'total':<12}{total:>8.1f}")
        if module is not None and self.wants("fences"):
            fences = module.get("fences", [])
            lines.append(f"[debug] fences: {len(fences)}")
            for fence in fences:
                span = fence.get("span", {})
                lines.append(
                    f"[debug]   {span.get('file')}:{span.get('line')}:{span.get('col')}"
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
        return "\n".join(lines)

    def summary(self, module: dict[str, Any] | None, outputs: dict[str, str], status: str) -> dict[str, Any]:
        return {
            "stages": [{"name": name, "ms": round(ms, 3)} for name, ms in self.stages],
            "timings": self.timings(),
            "totalMs": round((time.perf_counter() - self._t0) * 1000.0, 3),
            "fences": [] if module is None else module.get("fences", []),
            "functions": 0 if module is None else len(module.get("functions", [])),
            "commands": self.commands,
            "outputs": outputs,
            "status": status,
            "verify": {
                "ir": self.ir_verifications,
                "llvm": self.llvm_verification,
            },
        }

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
