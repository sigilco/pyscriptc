"""Load the real program corpus as compat2 Probe objects.

Conventions the scorer relies on (the Probe contract in outcomes.py is not extended):

- scripts, category "program": `source` is main.py, `call` is "" (run the whole program) and
  `args == (expected_stdout,)` where expected_stdout is expected.txt from CPython 3.14.
  Weight is 3.0 per program (`probe_weight`).
- top100, category "function": `source` is a thin wrapper that imports one closed signature function
  from the pinned package and returns its value, `call` is "probe". The scorer puts the pinned tree's
  parent directory on the import path (see `package_root`). The id is `top100.<project>.<qual>`.
  Weight is 2.0 per corpus function.
- top100, category "program": one per package, `source` imports the package, `call` is "" and
  `args == ("",)`. The scorer compiles the pinned tree itself, as v1 `classify_tree` does.
  Weight is 5.0 per whole package.

The holdout is loaded only with `holdout=True` (CLI `--holdout`). The default never opens it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from benchmarks.compat2.outcomes import Probe, probe_weight

HERE = Path(__file__).resolve().parent
EVALUATOR_ENV = "PYSCRIPTC_EVALUATOR"


def holdout_open() -> bool:
    """True only for the evaluator: the env flag is set and the holdout is on disk.

    Generator agents never get the flag (see .claude/hooks/holdout_guard.py and .agents/docker/run.sh), and
    their containers see an empty directory over the holdout.
    """

    return os.environ.get(EVALUATOR_ENV) == "1" and (HERE / "holdout" / "HOLDOUT").is_file()


def _root(holdout: bool) -> Path:
    if holdout and not holdout_open():
        raise PermissionError(f"the holdout is closed: it opens only for the evaluator ({EVALUATOR_ENV}=1)")
    return HERE / "holdout" if holdout else HERE


def script_probes(holdout: bool = False) -> list[Probe]:
    probes = []
    for folder in sorted((_root(holdout) / "scripts").glob("*/")):
        main, expected = folder / "main.py", folder / "expected.txt"
        if not (main.is_file() and expected.is_file()):
            continue
        probes.append(
            Probe(
                id=f"scripts.{folder.name}",
                area="scripts",
                category="program",
                kind="value",
                source=main.read_text(encoding="utf-8"),
                call="",
                args=(expected.read_text(encoding="utf-8"),),
                weight=probe_weight("program", area="scripts"),
            )
        )
    return probes


def package_pins(holdout: bool = False) -> dict[str, dict[str, object]]:
    return json.loads((_root(holdout) / "pins.json").read_text(encoding="utf-8"))["packages"]


def package_root(pin: dict[str, object], holdout: bool = False) -> Path:
    """The pinned tree (a directory, or a single .py file) on disk."""

    return (_root(holdout) / str(pin["tree"])).resolve()


def import_roots(holdout: bool = False) -> list[Path]:
    """Parent directories of every pinned tree and support dependency, for the CPython oracle path."""

    visible = json.loads((HERE / "pins.json").read_text(encoding="utf-8"))
    pins = [*visible["packages"].values(), *visible.get("support", {}).values()]
    roots = [package_root(pin).parent for pin in pins]
    if holdout:
        roots += [package_root(pin, True).parent for pin in package_pins(True).values()]
    return list(dict.fromkeys(roots))


def _wrapper(fn: dict[str, object]) -> str:
    args = ", ".join(repr(a) for a in fn["call_args"])  # type: ignore[attr-defined]
    head = f"from {fn['module']} import {fn['name']}"
    return f"{head}\n\n\ndef probe() -> {fn['returns']}:\n    return {fn['name']}({args})\n"


def package_probes(holdout: bool = False) -> list[Probe]:
    spec_path = _root(holdout) / "package_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))["packages"]
    probes = []
    for project, pin in sorted(package_pins(holdout).items()):
        import_name = str(pin["import_name"])
        probes.append(
            Probe(
                id=f"top100.{project}",
                area="top100",
                category="program",
                kind="value",
                source=f"import {import_name}\n",
                call="",
                args=("",),
                weight=probe_weight("program", area="top100"),
            )
        )
        for fn in spec.get(project, {}).get("closed_functions", []):
            probes.append(
                Probe(
                    id=f"top100.{project}.{fn['qual']}",
                    area="top100",
                    category="function",
                    kind="value",
                    source=_wrapper(fn),
                    weight=probe_weight("function", area="top100"),
                )
            )
    return probes


def load(holdout: bool = False) -> list[Probe]:
    return [*script_probes(holdout), *package_probes(holdout)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--holdout", action="store_true", help="load the held out set (epic boundaries only)")
    parser.add_argument("--json", action="store_true", help="print probes as JSON lines")
    ns = parser.parse_args(argv)
    probes = load(ns.holdout)
    if ns.json:
        for probe in probes:
            print(json.dumps(probe.to_json()))
        return 0
    kinds: dict[str, int] = {}
    for probe in probes:
        key = f"{probe.area}/{probe.category}"
        kinds[key] = kinds.get(key, 0) + 1
    print(json.dumps({"holdout": ns.holdout, "total": len(probes), **kinds}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
