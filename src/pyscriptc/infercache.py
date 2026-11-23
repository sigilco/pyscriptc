# SPDX-License-Identifier: Apache-2.0
"""Per-module inference-fact cache (issue #1318, option P1 of the inference
perf note `.agents/docs/design/inference-perf-baseline.md`).

`inferrunner.build_facts` pays one checker round trip per module on every
invocation, so a repeat compile of an unchanged tree pays the full cost
again. This module persists the per-module call-site facts under the
pyscriptc cache root (`infer/`, next to the `bin` run cache) and loads
them back when every input the facts depend on is unchanged.

The key follows the design note's rule: a cached entry is valid only
while the module's own source hash, the compiler digest
(`runcache.compiler_digest`, the same stamp the run cache keys on), the
content hashes of the in-tree modules it imports from, and the checker
binary (resolved argv plus its reported version) are all unchanged. Any
one of them moving recomputes the entry; a hit skips the checker round
trip for that module, and its call sites ride the pin reduction
unchanged.

The pin reduction (`pins_from_calls`) still runs over the merged
per-module call lists: the pins are a tree-wide fact by design, so a
cached entry can only contribute a call list the reduction re-derives,
never a pin the checker did not prove. A cached call site keeps the
positional-index spelling of its arguments (`#0`, `x`), so a callee
whose signature changed still maps to the new parameter names through
the fresh signature map the reduction reads. A cache write is atomic
(temp file plus rename), so concurrent compiles never observe a partial
entry, and a corrupt entry is a miss, never a failed compile.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pyscriptc.inferfacts import CallSiteFacts
from pyscriptc.typefacts import TypeFacts

# The cache schema version: bump it when the stored record shape changes
# in a way older entries cannot satisfy.
SCHEMA = 1


def cache_dir() -> Path:
    """The inference-fact cache directory, under the pyscriptc cache root."""
    from pyscriptc.runcache import cache_root

    return cache_root() / "infer"


def enabled() -> bool:
    """Whether the cache is on (`PYSC_NO_INFER_CACHE=1` bypasses it)."""
    return os.environ.get("PYSC_NO_INFER_CACHE", "") not in ("1", "true", "yes")


_CHECKER_STAMP: str | None = None


def checker_stamp() -> str:
    """The checker identity: the resolved argv plus the version it reports.

    A checker upgrade changes what the facts are, so it invalidates every
    entry, the way a compiler change does for the run cache. The stamp is
    read once per process: the version of an installed binary does not
    change under a running compile.
    """
    global _CHECKER_STAMP
    if _CHECKER_STAMP is not None:
        return _CHECKER_STAMP
    from pyscriptc.check import _resolve_pyrefly_binary

    argv = list(_resolve_pyrefly_binary())
    version = ""
    try:
        proc = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=60)
        version = (proc.stdout or "").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        version = ""
    _CHECKER_STAMP = hashlib.sha256(f"{' '.join(argv)}\n{version}".encode()).hexdigest()
    return _CHECKER_STAMP


def _facts_key(module_source: bytes, module: str, dep_hashes: dict[str, str]) -> str:
    """The cache key: schema, compiler digest, checker stamp, module and dep hashes."""
    from pyscriptc.runcache import compiler_digest

    hasher = hashlib.sha256()
    hasher.update(str(SCHEMA).encode())
    hasher.update(compiler_digest().encode())
    hasher.update(checker_stamp().encode())
    hasher.update(hashlib.sha256(module_source).hexdigest().encode())
    hasher.update(module.encode())
    for name in sorted(dep_hashes):
        hasher.update(name.encode())
        hasher.update(dep_hashes[name].encode())
    return hasher.hexdigest()[:32]


def tree_modules(root: Path) -> dict[str, Path]:
    """The tree's module names mapped to their files."""
    from pyscriptc.inferrunner import _module_name

    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        modules.setdefault(_module_name(path, root), path)
    return modules


def _resolve_dependency(name: str, modules: dict[str, Path]) -> tuple[str, Path] | None:
    """The tree module a qualified imported name refers to, if any.

    The imported name is a source-level spelling (`lib.helper` for
    `from lib import helper`, `lib` for `import lib`), while the tree's
    module names carry the package prefix (`pkg.lib`), so both the name
    itself and the module it names are tried, longest match first.
    """
    candidates = [name]
    head, _, _tail = name.rpartition(".")
    while head:
        candidates.append(head)
        head, _, _tail = head.rpartition(".")
    for candidate in candidates:
        for module_name, path in modules.items():
            if module_name == candidate or module_name.endswith("." + candidate):
                return module_name, path
    return None


def dependency_hashes(tree: ast.Module, module: str, modules: dict[str, Path]) -> dict[str, str]:
    """The content hashes of the in-tree modules this module imports from.

    The design note scopes the dependency set to the direct imports
    (`_imported_names`), not the transitive closure: the pin reduction
    re-derives tree-wide agreement from the per-module call lists, so
    staleness stays local. `modules` is the tree's module-name map
    (`tree_modules`), built once per run. A callee the tree does not
    resolve to a file (a builtin, a stdlib module, a dependency source
    root) contributes no hash: its facts live outside the tree, so a
    change there moves the compiler digest or the dependency root, not
    this key.
    """
    from pyscriptc.inferrunner import _imported_names

    dep_hashes: dict[str, str] = {}
    for name in sorted(set(_imported_names(tree, module).values())):
        found = _resolve_dependency(name, modules)
        if found is None:
            continue
        module_name, path = found
        try:
            dep_hashes[module_name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return dep_hashes


def _encode_fact(fact: TypeFacts) -> dict[str, Any]:
    return {
        "kind": fact.kind,
        "optional": fact.optional,
        "args": [_encode_fact(arg) for arg in fact.args],
        "runtime_repr": fact.runtime_repr,
    }


def _decode_fact(data: Any) -> TypeFacts:
    return TypeFacts(
        kind=data["kind"],
        optional=bool(data.get("optional", False)),
        args=tuple(_decode_fact(arg) for arg in data.get("args", ())),
        runtime_repr=data.get("runtime_repr"),
    )


def record_to_json(calls: list[CallSiteFacts]) -> dict[str, Any]:
    """The JSON-serializable form of one module's call sites."""
    return {
        "schema": SCHEMA,
        "calls": [
            {
                "module": call.module,
                "callee": call.callee,
                "lineno": call.lineno,
                "col_offset": call.col_offset,
                "params": {name: _encode_fact(fact) for name, fact in call.params.items()},
            }
            for call in calls
        ],
    }


def record_from_json(data: Any) -> list[CallSiteFacts]:
    """The call sites back from their stored form.

    Raises `ValueError` on a shape mismatch, so a stale or damaged entry
    is a miss rather than a failed compile.
    """
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("inference cache: schema mismatch")
    raw_calls = data.get("calls")
    if not isinstance(raw_calls, list):
        raise ValueError("inference cache: no call list")
    calls: list[CallSiteFacts] = []
    for raw in raw_calls:
        params = {name: _decode_fact(fact) for name, fact in raw["params"].items()}
        calls.append(
            CallSiteFacts(
                module=raw["module"],
                callee=raw["callee"],
                lineno=int(raw["lineno"]),
                col_offset=int(raw["col_offset"]),
                params=params,
            )
        )
    return calls


def load(source: bytes, module: str, dep_hashes: dict[str, str]) -> list[CallSiteFacts] | None:
    """The module's cached call sites, or None when the entry is absent or stale."""
    if not enabled():
        return None
    key = _facts_key(source, module, dep_hashes)
    entry = cache_dir() / f"{key}.json"
    try:
        return record_from_json(json.loads(entry.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(source: bytes, module: str, dep_hashes: dict[str, str], calls: list[CallSiteFacts]) -> None:
    """Persist one module's call sites under its content key. Best effort.

    A module the checker could not read contributes no entry: an empty
    call list there would hide a later successful run.
    """
    if not enabled():
        return
    key = _facts_key(source, module, dep_hashes)
    entry = cache_dir() / f"{key}.json"
    tmp = entry.with_name(f".{key}.{os.getpid()}.tmp")
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record_to_json(calls)), encoding="utf-8")
        tmp.replace(entry)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
