"""Run probes under the interpreter that executes this module and print their raw oracle results.

The generator starts one of these per CPython 3.10 to 3.14 and per hash seed, then keeps for each probe the
versions whose result equals the oracle's. This file must stay parseable by 3.10.

    python -m benchmarks.compat2.probes.versions PROBES.jsonl   one `[id, status, value]` JSON line per probe
    python -m benchmarks.compat2.probes.versions --ast           concrete ast node names, as a JSON list
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile

from .engine import run_forked


def _call(source: str, call: str, args: list[object]) -> object:
    namespace: dict[str, object] = {}
    exec(compile(source, "<probe>", "exec"), namespace)
    return namespace[call](*args)  # type: ignore[operator]


def ast_nodes() -> list[str]:
    return sorted(
        name
        for name in dir(ast)
        if not name.startswith("_") and isinstance(getattr(ast, name), type) and issubclass(getattr(ast, name), ast.AST)
    )


def main(argv: list[str]) -> int:
    if argv[1] == "--ast":
        print(json.dumps(ast_nodes()))
        return 0
    sys.setrecursionlimit(2000)
    os.chdir(tempfile.mkdtemp(prefix="ps-versions-"))
    with open(argv[1], encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            # timeout and crash can be load flakes; run_forked already retries them
            # internally, so a real crash reproduces and a flake does not survive.
            status, value = run_forked(lambda r=row: _call(r["source"], r["call"], r["args"]))
            print(json.dumps([row["id"], status, value]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
