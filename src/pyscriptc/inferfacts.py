"""Cross-module inference facts: what the checker proved at each call site.

The checker bridge (`factsbridge.py`) proves types inside one module. This
module carries the other half: what the checker resolved at the call sites
of a package, so a parameter that no module annotates can still be typed
from the arguments its callers actually pass.

The soundness rule is the same as everywhere else in the compiler: a
parameter is pinned only when every call site in the package proves the
same concrete type, and the type is one the lowered subset can express.
A call site the checker could not resolve, a call site that disagrees, and
a type outside the subset all leave the parameter fenced (`PS1001`).
Nothing here guesses.

`CallSiteFacts` is the per-call-site record: which module and which
qualified callee the call names, the parameter each argument binds, and
the checker's own rendering of the argument's type. `InferFacts` is the
package-wide map: `calls` lists every call site, and `pins` holds the
parameters the package agrees on, with the proof that made them.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from pyscriptc.typefacts import TypeFacts

# The IR kinds a pinned parameter may carry. A union with more than one
# arm is not in the IR, so it stays fenced; `Any` is never a fact.
_PINNABLE = {"int", "float", "bool", "str", "bytes", "none", "list", "dict", "set", "tuple", "class"}


@dataclass(frozen=True)
class CallSiteFacts:
    """One resolved call site: what the checker proved about each argument.

    `module` is the file stem the call lives in, `callee` the qualified
    name it names (`pkg.mod.func` for an imported one, `mod.func` for a
    same-package one). `params` maps the callee's parameter name to the
    `TypeFacts` of the argument passed there.
    """

    module: str
    callee: str
    lineno: int
    col_offset: int
    params: dict[str, TypeFacts] = field(default_factory=dict)

    def signature_key(self) -> str:
        """The callee this call site contributes a fact to."""
        return self.callee


@dataclass(frozen=True)
class PinFacts:
    """One parameter the package agrees on, with the proof that made it.

    `kind` is the checker's rendering of the agreed type ("int",
    "list[str]"). `sites` counts the call sites that agreed.
    """

    module: str
    callee: str
    param: str
    kind: str
    sites: int


@dataclass
class InferFacts:
    """The package-wide inference facts the lowerer consumes."""

    calls: list[CallSiteFacts] = field(default_factory=list)
    pins: dict[tuple[str, str], PinFacts] = field(default_factory=dict)
    provider: str = ""

    def pins_for(self, callee: str) -> dict[str, PinFacts]:
        """The pinned parameters of one qualified callee, by parameter name."""
        return {param: pin for (name, param), pin in self.pins.items() if name == callee}

    def pin_summary(self) -> dict[str, int]:
        """Counts for the report: how many parameters the package pinned."""
        return {"call_sites": len(self.calls), "pinned_params": len(self.pins)}


def parse_pin_type(kind: str) -> TypeFacts | None:
    """Parse a pin's rendered kind back to a fact, or None when it is not pinnable.

    The render is the checker's own spelling (`int`, `list[str]`), which
    `facts_to_ir` already translates. A spelling the parser does not know
    returns None and the pin stays a fence, never a guess.
    """

    text = kind.strip()
    if text in {"int", "float", "bool", "str", "bytes", "none"}:
        return TypeFacts(kind=text, runtime_repr=text)
    if text == "None":
        return TypeFacts(kind="none", optional=True, runtime_repr="None")
    for open_, close, name in (("list[", "]", "list"), ("set[", "]", "set"), ("dict[", "]", "dict")):
        if text.startswith(open_) and text.endswith(close):
            inner = text[len(open_) : -1]
            parts = _split_top(inner)
            args = tuple(item for part in parts if (item := parse_pin_type(part)) is not None)
            if len(args) == len(parts) and parts:
                return TypeFacts(kind=name, args=args, runtime_repr=text)
            return None
    if text.startswith("tuple[") and text.endswith("]"):
        parts = _split_top(text[6:-1])
        args = tuple(item for part in parts if (item := parse_pin_type(part)) is not None)
        if len(args) == len(parts) and parts:
            return TypeFacts(kind="tuple", args=args, runtime_repr=text)
        return None
    if text and text[0].isupper() and "." not in text and "[" not in text:
        return TypeFacts(kind="class", runtime_repr=text)
    return None


def _split_top(text: str) -> list[str]:
    """Split a type list on top-level commas, respecting brackets."""

    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _widen(facts: TypeFacts) -> TypeFacts:
    """A literal fact widens to its base type (`Literal[1]` to `int`)."""
    if facts.kind == "literal" and facts.args:
        return facts.args[0]
    return facts


def _render(facts: TypeFacts) -> str:
    """The checker's own rendering of a fact, for the record and the report."""
    if facts.runtime_repr:
        return str(facts.runtime_repr)
    if facts.kind == "list" and len(facts.args) == 1:
        return f"list[{_render(facts.args[0])}]"
    if facts.kind == "dict" and len(facts.args) == 2:
        return f"dict[{_render(facts.args[0])}, {_render(facts.args[1])}]"
    return facts.kind


def _same_shape(one: TypeFacts, other: TypeFacts) -> bool:
    """Whether two facts name the same type, ignoring the literal value."""
    if one.kind != other.kind:
        return False
    if one.optional != other.optional:
        return False
    if len(one.args) != len(other.args):
        return False
    return all(_same_shape(a, b) for a, b in zip(one.args, other.args, strict=True))


def _pinnable(facts: TypeFacts) -> bool:
    """Whether the subset can carry this fact as a parameter type."""
    if facts.kind in {"any", "unknown", "union", "typevar", "type", "callable", "overloaded"}:
        return False
    if facts.kind == "literal":
        return bool(facts.args) and _pinnable(facts.args[0])
    if facts.kind == "list":
        return len(facts.args) == 1 and _pinnable(facts.args[0])
    if facts.kind == "set":
        return len(facts.args) == 1 and _pinnable(facts.args[0])
    if facts.kind == "dict":
        return len(facts.args) == 2 and all(_pinnable(a) for a in facts.args)
    if facts.kind == "tuple":
        return bool(facts.args) and all(_pinnable(a) for a in facts.args)
    if facts.kind == "class":
        return not facts.args
    return facts.kind in _PINNABLE


def collect_calls(
    module: str,
    tree: ast.Module,
    callee_names: dict[str, str],
    expressions: dict[str, TypeFacts],
) -> list[CallSiteFacts]:
    """The call sites of one module that the checker resolved.

    `callee_names` maps the local name a call uses to the qualified callee
    it names (the caller resolved the import). A call through a name the
    caller could not resolve contributes nothing: the pin needs every
    call site to agree, and an unresolved call site is a disagreement.

    `expressions` is the module's own fact map, keyed `line:col`.
    """

    calls: list[CallSiteFacts] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        callee = callee_names.get(node.func.id)
        if callee is None:
            # A call the caller could not resolve (a name with no import
            # and no same-module def): contribute nothing. The pin does
            # not know this call exists, which is safe only because the
            # pin additionally requires the callee's whole signature to be
            # unannotated and the package map to carry a fact for every
            # call the checker did resolve; see `pins_from_calls`.
            continue
        facts = _argument_facts(node, expressions)
        if facts is None:
            # The checker could not resolve every argument (`Any`,
            # `**kwargs`): record the call with no params, so the pin
            # knows this callee has a call site it cannot prove and
            # refuses.
            calls.append(
                CallSiteFacts(
                    module=module,
                    callee=callee,
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    params={},
                )
            )
            continue
        calls.append(
            CallSiteFacts(
                module=module,
                callee=callee,
                lineno=node.lineno,
                col_offset=node.col_offset,
                params=facts,
            )
        )
    return calls


def _argument_facts(node: ast.Call, expressions: dict[str, TypeFacts]) -> dict[str, TypeFacts] | None:
    """The checker's fact for each argument of one call, or None.

    None means the call site could not be fully resolved (an argument the
    checker typed as `Any`, or a `**kwargs` spread): the pin that depends
    on this callee must refuse, so the caller records an empty-params
    `CallSiteFacts` instead of dropping the call.
    """

    params: dict[str, TypeFacts] = {}
    for index, arg in enumerate(node.args):
        facts = expressions.get(f"{arg.lineno}:{arg.col_offset}")
        if facts is None:
            return None
        params[f"#{index}"] = facts
    for kw in node.keywords:
        if kw.arg is None:
            # `**kwargs` at a call site: the parameter it binds is not
            # knowable from the call, so the call site is unresolved.
            return None
        facts = expressions.get(f"{kw.value.lineno}:{kw.value.col_offset}")
        if facts is None:
            return None
        params[kw.arg] = facts
    return params


def pins_from_calls(
    calls: list[CallSiteFacts],
    signatures: dict[str, dict[str, str]],
) -> dict[tuple[str, str], PinFacts]:
    """The parameters every call site agrees on.

    `signatures` maps a qualified callee name to its positional-index and
    keyword parameter names. A parameter is pinned when every call site
    that binds it proves the same concrete type; a call site the checker
    did not resolve, or one that binds the parameter to a different type,
    refuses the pin. The returned key is `(qualified callee, parameter
    name)`; the parameter name is the callee's own spelling (`a`, `b`),
    not the positional index.
    """

    # A callee with a call site the checker could not prove (an argument
    # typed `Any`, a `**kwargs` spread, or a signature the map does not
    # know) gets no pins at all: the soundness rule is the pin's own,
    # every call site must agree, and an unprovable call site does not
    # agree.
    unresolved: set[str] = set()
    for call in calls:
        if not call.params:
            unresolved.add(call.callee)
        elif call.signature_key() not in signatures:
            unresolved.add(call.callee)
    seen: dict[tuple[str, str], TypeFacts] = {}
    counts: dict[tuple[str, str], int] = {}
    refused: set[tuple[str, str]] = set()
    for call in calls:
        if call.callee in unresolved:
            continue
        signature = signatures.get(call.signature_key())
        if signature is None:
            continue
        for index, facts in call.params.items():
            # Translate the call-site index (`#0`) to the callee's own
            # parameter spelling; a keyword argument already spells it.
            param = signature.get(index, index)
            key = (call.callee, param)
            widened = _widen(facts)
            if not _pinnable(widened):
                refused.add(key)
                continue
            known = seen.get(key)
            if known is None:
                seen[key] = widened
                counts[key] = 1
            elif _same_shape(known, widened):
                counts[key] += 1
            else:
                refused.add(key)
    pins: dict[tuple[str, str], PinFacts] = {}
    for key, facts in seen.items():
        if key in refused:
            continue
        callee, param = key
        pins[key] = PinFacts(
            module=callee.rpartition(".")[0],
            callee=callee,
            param=param,
            kind=_render(facts),
            sites=counts[key],
        )
    return pins
