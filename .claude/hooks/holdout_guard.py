#!/usr/bin/env python3
"""PreToolUse hook: keep generator agents out of the scorer holdout.

The holdout stays committed so the workflow is reproducible, but only the evaluator may read it.
The evaluator is a session started with PYSCRIPTC_EVALUATOR=1 in its environment. Agents cannot set it:
this hook runs in the Claude process, not in an agent's shell, and commands that mention the variable are blocked.

This is a guardrail against accidental and incentive driven access by cooperating agents, not a sandbox
against a hostile one. The layers are listed in benchmarks/compat2/corpus/README.md.

Exit 2 blocks the call and sends stderr back to the agent.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HOLDOUT_PARTS = ("benchmarks", "compat2", "corpus", "holdout")
PATH_KEYS = ("file_path", "notebook_path", "path")
GLOB_KEYS = ("glob", "pattern")
# a command that mentions the holdout, the evaluator flag, or dumps git objects by hash
BASH_BLOCK = re.compile(r"holdout|PYSCRIPTC_EVALUATOR|\bgit\s+(cat-file|ls-tree|archive)\b", re.IGNORECASE)
MESSAGE = (
    "blocked: the scorer holdout is closed to generator agents. Work from the visible corpus "
    "(benchmarks/compat2/corpus) and the support ledger. Only the evaluator session reads the holdout."
)


def under_holdout(raw: str, cwd: str) -> bool:
    path = Path(os.path.normpath(os.path.join(cwd, os.path.expanduser(raw))))
    parts = path.parts
    n = len(HOLDOUT_PARTS)
    return any(parts[i : i + n] == HOLDOUT_PARTS for i in range(len(parts) - n + 1))


def mentions_holdout(value: str) -> bool:
    return bool(re.search(r"corpus[/\\]+holdout|holdout[/\\]", value, re.IGNORECASE))


def blocked(event: dict[str, object]) -> bool:
    tool = str(event.get("tool_name", ""))
    args = event.get("tool_input") or {}
    cwd = str(event.get("cwd") or os.getcwd())
    if not isinstance(args, dict):
        return False
    if tool == "Bash":
        return bool(BASH_BLOCK.search(str(args.get("command", ""))))
    for key in PATH_KEYS:
        if isinstance(args.get(key), str) and under_holdout(args[key], cwd):
            return True
    for key in GLOB_KEYS:
        if tool in {"Glob", "Grep"} and isinstance(args.get(key), str) and mentions_holdout(args[key]):
            return True
    return False


def main() -> int:
    if os.environ.get("PYSCRIPTC_EVALUATOR") == "1":
        return 0
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if blocked(event):
        print(MESSAGE, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
