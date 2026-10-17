"""Compiler debug mode.

`--debug` (or `PYSC_DEBUG`) prints a compact per-stage table to stderr.
`--debug-dir DIR` additionally writes the intermediate artifacts the agent
needs to inspect a compile: the lowered module (`ir.json`), the emitted LLVM
text (`out.ll`), the exact cc command lines (`cc.sh`), and a machine-readable
`summary.json`. Debug output never touches the IR or the emitted text.

"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_TOPICS = {"stages", "fences", "cc", "paths", "all"}


def parse_topics(value: str | None) -> set[str]:
    """A `--debug`/`PYSC_DEBUG` value: empty means `stages`, else a comma list."""
    if value is None or not value.strip() or value.strip() in ("1", "true", "on"):
        return {"stages"}
    topics = {topic.strip() for topic in value.split(",") if topic.strip()}
    unknown = topics - _TOPICS
    if unknown:
        raise ValueError(f"unknown debug topic(s): {', '.join(sorted(unknown))}")
    return topics


def resolve(flag: str | None, debug_dir: str | None) -> Debug | None:
    """`--debug` wins over `PYSC_DEBUG`; `--debug-dir` alone also enables debug."""
    value = flag if flag is not None else os.environ.get("PYSC_DEBUG")
    if flag is None and not value and debug_dir is None:
        return None
    topics = parse_topics(value)
    if debug_dir is not None:
        topics.add("paths")
    return Debug(topics, None if debug_dir is None else Path(debug_dir))


class Debug:
    def __init__(self, topics: set[str], debug_dir: Path | None) -> None:
        self.topics = topics
        self.debug_dir = debug_dir
        self.stages: list[tuple[str, float]] = []
        self.commands: list[list[str]] = []
        self._starts: list[float] = []
        self._t0 = time.perf_counter()

    def wants(self, topic: str) -> bool:
        return "all" in self.topics or topic in self.topics

    def begin(self, name: str) -> None:
        self._starts.append(time.perf_counter())

    def end(self, name: str) -> None:
        self.stages.append((name, (time.perf_counter() - self._starts.pop()) * 1000.0))

    def render(self, module: dict[str, Any] | None, outputs: dict[str, str]) -> str:
        lines: list[str] = []
        if self.wants("stages"):
            lines.append("[debug] stage           ms")
            for name, ms in self.stages:
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
        return "\n".join(lines)

    def summary(self, module: dict[str, Any] | None, outputs: dict[str, str], status: str) -> dict[str, Any]:
        return {
            "stages": [{"name": name, "ms": round(ms, 3)} for name, ms in self.stages],
            "totalMs": round((time.perf_counter() - self._t0) * 1000.0, 3),
            "fences": [] if module is None else module.get("fences", []),
            "functions": 0 if module is None else len(module.get("functions", [])),
            "commands": self.commands,
            "outputs": outputs,
            "status": status,
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
