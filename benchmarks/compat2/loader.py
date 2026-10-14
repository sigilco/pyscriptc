"""Probe loader shared by Track C (producer) and the scorer (consumer).

Format: one JSON object per line, the fields of ``Probe.to_json``. ``args`` is a
JSON list. A directory is read as every ``*.jsonl`` file inside it, sorted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from benchmarks.compat2.outcomes import Probe


def load_probes(path: str | Path) -> Iterable[Probe]:
    root = Path(path)
    files = sorted(root.rglob("*.jsonl")) if root.is_dir() else [root]
    return list(_read(files))


def _read(files: list[Path]) -> Iterator[Probe]:
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                data["args"] = tuple(data.get("args", ()))
                yield Probe(**data)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{file}:{number}: bad probe: {exc}") from exc


def dump_probes(path: str | Path, probes: Iterable[Probe]) -> None:
    lines = [json.dumps(probe.to_json(), sort_keys=True) for probe in probes]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
