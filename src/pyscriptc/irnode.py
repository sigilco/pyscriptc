"""Prototype (not for merge): a dataclass IR node for the typed-subset assessment.

The question this file feeds: can the compiler's own IR stop being
``dict[str, Any]`` and become a dataclass, so the 638 PS1104 fences on IR
annotations can name a real type?

Design constraints measured against:

- The canonical JSON must stay byte-identical (frozen gate
  ``benchmarks/compat2/probes/IR_HASHES.json``, contract in
  ``.agents/docs/spec/ir-canonicity.md``). ``Node.as_dict()`` reproduces the
  exact dict shape the lowerer builds today, key for key, and the pipeline
  converts back to dicts at the ``json.dumps`` boundary, so the hash never
  sees a dataclass.
- The subset refuses ``A | B`` unions (PS1101), so a per-kind class
  hierarchy (``Name(Node)``, ``Const(Node)`` ...) cannot be annotated as a
  parameter type unless every arm is accepted, which the subset cannot
  express. One node class with a ``kind`` tag and typed optional fields is
  the only shape the subset itself could one day compile.
- Optional fields carry ``T | None``, which the subset *does* lower (the
  boxed optional layout), so this file is subset-legal typing-wise.

This module is imported by nothing on the hot path; it exists so the
prototype diff on ``lower/_core.py`` has something to reference and so the
fence count can be measured with and without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    """One IR node: the ``kind`` tag plus every field any kind carries.

    Fields are typed as ``Node | None`` / ``list[Node]`` where the IR is
    recursive, and left as ``Any`` for the scalar payloads (``value`` is
    ``str | int | float | bool | None`` depending on kind, which the subset
    cannot express either). ``type`` and ``span`` stay dicts: the type
    vocabulary is a separate, smaller recursive shape that a follow-up could
    give the same treatment.
    """

    kind: str
    type: dict[str, Any] | None = None
    span: dict[str, Any] | None = None
    # name
    id: str = ""
    # const
    value: Any = None
    # unary / binary / compare
    op: str = ""
    operand: Node | None = None
    left: Node | None = None
    right: Node | None = None
    # call
    callee: dict[str, Any] | None = None
    args: list[Node] | None = None
    symbol: str | None = None
    # list / tuple-const
    elems: list[Node] | None = None
    # dict
    pairs: list[dict[str, Any]] | None = None
    # subscript / attr
    index: Node | None = None
    name: str = ""
    # cast
    # contains
    hay: Node | None = None
    needle: Node | None = None
    negated: bool = False
    # statements
    target: str = ""
    cond: Node | None = None
    then: list[Node] | None = None
    else_: list[Node] | None = None
    body: list[Node] | None = None
    handlers: list[dict[str, Any]] | None = None
    expr: Node | None = None
    container: Node | None = None
    object: Node | None = None
    exc: list[str] | None = None
    message: Node | None = None
    code: int | None = None
    # module-closed markers
    identity: bool = False
    frozen: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Reproduce the exact dict the lowerer builds today, key for key.

        Only keys the kind actually carries are emitted, in the same
        insertion order the dict literal had, so ``json.dumps(as_dict())``
        is byte-identical to the current IR JSON.
        """
        out: dict[str, Any] = {"kind": self.kind}
        for key in _KEY_ORDER[self.kind]:
            val = getattr(self, _ATTR.get(key, key))
            if val is _ABSENT[key] or val is None and key not in _NULLABLE[self.kind]:
                continue
            out[key] = _unwrap(val)
        if self.type is not None:
            out["type"] = self.type
        if self.span is not None:
            out["span"] = self.span
        out.update(self.extra)
        return out


def _unwrap(val: Any) -> Any:
    if isinstance(val, Node):
        return val.as_dict()
    if isinstance(val, list):
        return [_unwrap(v) for v in val]
    if isinstance(val, dict):
        return {k: _unwrap(v) for k, v in val.items()}
    return val


# Per-kind key order, mirroring the dict literals in lower/*.py today.
# ``type`` and ``span`` are appended last by as_dict, matching the literals
# that put them last.
_KEY_ORDER: dict[str, tuple[str, ...]] = {
    "name": ("id",),
    "const": ("value",),
    "unary": ("op", "operand"),
    "binary": ("op", "left", "right"),
    "compare": ("op", "left", "right"),
    "call": ("callee", "args", "symbol"),
    "list": ("elems",),
    "tuple-const": ("elems",),
    "dict": ("pairs",),
    "subscript": ("value", "index"),
    "attr": ("value", "name"),
    "cast": ("op", "value"),
    "contains": ("hay", "needle", "negated"),
    "assign": ("target", "value"),
    "if": ("cond", "then", "else"),
    "while": ("cond", "body"),
    "return": ("value",),
    "expr": ("expr",),
    "pass": (),
    "setitem": ("container", "index", "value"),
    "setattr": ("object", "name", "value"),
    "try": ("body", "handlers"),
    "raise": ("exc", "message", "code"),
}

# Which keys may legitimately be None in the output (e.g. raise message).
_NULLABLE: dict[str, frozenset[str]] = {
    "raise": frozenset({"message", "code"}),
    "return": frozenset({"value"}),
    "call": frozenset({"symbol"}),
}

# Sentinel defaults per key: a key whose attribute still holds the default
# is absent from the dict. ``None`` defaults mean "absent unless nullable".
_ABSENT: dict[str, Any] = {
    "id": "",
    "op": "",
    "name": "",
    "target": "",
    "negated": False,
    "value": None,
}

# Attribute renames for keys that clash with Python keywords or builtins.
_ATTR: dict[str, str] = {"else": "else_"}
