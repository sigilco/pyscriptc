"""Canonical form of the JSON IR and its stable hash.

Probes compare compiler runs across invocations, so the IR they digest must
not carry anything incidental: spans name the invocation directory, the
cpython stamp tracks the interpreter rather than the compiler, and package
merge notes are stderr material. The rules and the full key vocabulary live
in docs/ir-canonicity.md; this module is that document in code.

The hash is schema-versioned (the sccache FORMAT_VERSION pattern): a change
to the canonicalization rules rekeys every digest instead of masquerading as
a compiler change.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

CANONICAL_SCHEMA_VERSION = "canonical-schema/1"

# Keys removed at every nesting depth. `span` rides on functions, classes,
# statements, expressions and fence records; the rest are module level only.
_STRIP_KEYS = frozenset({"span", "sourceFile", "cpython", "_notes"})


def canonicalize(module: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical deep copy: incidental keys stripped, orders normalized.

    - every `span`, plus top-level `sourceFile`, `cpython`, `_notes`, gone;
    - `classes` sorted by `name` (synthetic class order leaks a set iteration
      order at lower.py:607);
    - every try-handler's `codes` list sorted (same leak via self.classes);
    - `fences` sorted by (code, message, function or "", span.line, span.col),
      computed before spans are stripped because `function` may be None.

    Idempotent: canonicalize(canonicalize(x)) == canonicalize(x). The input
    dict is never mutated.
    """
    out = _strip(module)
    classes = out.get("classes")
    if isinstance(classes, list):
        out["classes"] = sorted(classes, key=lambda cls: cls.get("name") or "")
    for stmt in _walk_stmts(out):
        for handler in stmt.get("handlers", ()):
            if isinstance(handler, dict) and isinstance(handler.get("codes"), list):
                handler["codes"] = sorted(handler["codes"])
    fences = out.get("fences")
    if isinstance(fences, list):
        out["fences"] = sorted(
            fences,
            key=lambda fence: (
                fence.get("code") or "",
                fence.get("message") or "",
                fence.get("function") or "",
                fence.get("span", {}).get("line", 0),
                fence.get("span", {}).get("col", 0),
            ),
        )
    return out


def hash_module(module: dict[str, Any]) -> str:
    """SHA-256 hex of the schema-versioned canonical form of a module IR dict."""
    canonical = {
        "schema": CANONICAL_SCHEMA_VERSION,
        "ir": canonicalize(module),
    }
    # allow_nan=False on purpose: a NaN reaching IR means a compiler bug and
    # should surface here instead of silently hashing into a digest.
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _strip(value: Any) -> Any:
    """Deep copy `value` dropping every _STRIP_KEYS member at any depth."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in _STRIP_KEYS}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    if isinstance(value, tuple):
        return [_strip(item) for item in value]
    return copy.copy(value)


def _walk_stmts(value: Any):
    """Yield every dict that looks like a statement node, recursively."""
    if isinstance(value, dict):
        if isinstance(value.get("kind"), str) and "handlers" in value:
            yield value
            for handler in value["handlers"]:
                yield from _walk_stmts(handler.get("body"))
        else:
            for child in value.values():
                yield from _walk_stmts(child)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_stmts(item)
