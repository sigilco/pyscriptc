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
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

CANONICAL_SCHEMA_VERSION = "canonical-schema/2"

# Schema/2: environment-derived absolute paths reaching IR string constants
# (stdmore.py reads sys.prefix / sys.executable at compile time) are replaced
# with this placeholder so the digest cannot depend on the checkout or venv
# location. Decision 2026-10-16.
_PATH_PLACEHOLDER = "<PATH>"

# Keys removed at every nesting depth. `span` rides on functions, classes,
# statements, expressions and fence records; the rest are module level only.
_STRIP_KEYS = frozenset({"span", "sourceFile", "cpython", "_notes"})


def canonicalize(module: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical deep copy: incidental keys stripped, orders normalized.

    - every `span`, plus top-level `sourceFile`, `cpython`, `_notes`, gone;
    - every substring of an environment absolute path (sys.executable,
      sys._base_executable, sys.exec_prefix, sys.prefix, cwd, the pyscriptc
      checkout root) in any string value is replaced by `<PATH>`; longest
      first so an executable path is consumed before its prefix runs;
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
    return _scrub_paths(out)


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


@lru_cache(maxsize=1)
def _env_paths() -> tuple[str, ...]:
    """Every absolute path this environment could leak into IR strings.

    Realpath and as-given variants both (macOS /tmp is a symlink), longest
    first so a path nested under another is replaced before its container.
    Cached: sys.prefix etc. cannot change inside one process.
    """

    roots: list[str] = []
    for raw in (
        getattr(sys, "executable", ""),
        getattr(sys, "_base_executable", ""),
        getattr(sys, "exec_prefix", ""),
        getattr(sys, "prefix", ""),
        os.getcwd(),
        _checkout_root(),
    ):
        if not raw:
            continue
        roots.append(os.path.normpath(raw))
        real = os.path.realpath(raw)
        if real != roots[-1]:
            roots.append(real)
    # Drop paths contained in a longer candidate: replacing the container
    # first would strand the remainder of the shorter one as plain text.
    return tuple(sorted(set(roots), key=len, reverse=True))


def _checkout_root() -> str:
    """Repository root found by walking up from this file to a pyproject.toml."""

    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return str(parent)
    return ""


def _scrub_paths(value: Any) -> Any:
    """Replace environment path substrings in every string value (keys are
    structural: the IR key vocabulary is fixed and never carries paths)."""

    if isinstance(value, dict):
        return {k: _scrub_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_paths(item) for item in value]
    if isinstance(value, str):
        for path in _env_paths():
            if path in value:
                value = value.replace(path, _PATH_PLACEHOLDER)
        return value
    return value


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
