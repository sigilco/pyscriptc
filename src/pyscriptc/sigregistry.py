"""Typed-signature registry over discovered stubs (partial slice, not wired yet).

`SignatureRegistry` answers two questions for a `(module, qualname)` pair:

- `signature()`: the declared `StubSignature` (all overloads, as `TypeFacts`),
  following explicit re-exports and `from m import *` across stub files.
- `lowering()`: the same signature mapped onto the lowerer's type dicts
  (`{"kind": "int"}`, `{"kind": "list", "elem": ...}`), in the shape the
  closed-table rows in `stdlib_queue` use, or a `Refusal` naming why it does
  not lower (`any`, `unannotated`, `ignored`, `varargs`, an unmapped type).

Lowering does not consume this yet. The integration points (where
`lower/_stdlib.py` would ask the registry before fencing PS2002 or PS2101)
are listed in the stubs/typeshed design notes. Hand-written
closed-table rows stay authoritative; the registry fills names they do not
cover and cross-checks the ones they do.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyscriptc.stdlib_queue import canonical_module
from pyscriptc.stubs import (
    STDLIB_SUBSET,
    TYPESHED_STDLIB,
    StubModule,
    StubRoot,
    StubSignature,
    Version,
    discover_roots,
    find_stub,
    load_stub,
)
from pyscriptc.typefacts import CallableFacts, ParameterFacts, StaticValue, TypeFacts, _parse_type_text

_SCALARS = frozenset({"int", "float", "bool", "str", "bytes"})
_LIST_HEADS = frozenset({"list", "List"})
_DICT_HEADS = frozenset({"dict", "Dict"})
_SET_HEADS = frozenset({"set", "Set"})
# typeshed buffer aliases accepted where the closed table already takes `buf`.
_BUFFER_ALIASES = frozenset({"ReadableBuffer", "Buffer"})
# Runtime-identical spellings: a `LiteralString` is a `str` once compiled.
_SCALAR_ALIASES = {"LiteralString": "str"}
_MAX_HOPS = 8


@dataclass(frozen=True)
class Refusal:
    """Why a declared signature has no lowered form. `reason` is a short stable code."""

    module: str
    qualname: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class LoweringSignature:
    """A stub signature in lowerer terms.

    `defaults` holds the `repr` of each optional parameter's stub default, in
    order, or None when the stub writes `...` (calls must then pass it).
    """

    module: str
    qualname: str
    params: tuple[dict[str, Any], ...]
    names: tuple[str, ...]
    returns: dict[str, Any]
    min_args: int
    defaults: tuple[str | None, ...]
    origin: str
    path: Path
    line: int

    def spec(self) -> dict[str, Any] | None:
        """`stdlib_queue`-shaped row (`_fixed` / `_optional`), or None when a default is unknown."""
        if any(default is None for default in self.defaults):
            return None
        row: dict[str, Any] = {"params": list(self.params), "return": self.returns}
        if self.defaults:
            row.update(min=self.min_args, defaults=list(self.defaults), names=list(self.names))
        return row


class SignatureRegistry:
    """Lazy, cached view over the stub roots for one compile."""

    def __init__(
        self, roots: Sequence[StubRoot], *, version: Version | None = None, platform: str | None = None
    ) -> None:
        self.roots = tuple(roots)
        self.version = version or sys.version_info[:2]
        self.platform = platform or sys.platform
        self._modules: dict[tuple[str, bool], StubModule | None] = {}

    @classmethod
    def for_source(
        cls,
        source: Path | None = None,
        stub_paths: Sequence[Path] = (),
        *,
        stdlib_allow: Iterable[str] | None = STDLIB_SUBSET,
        typeshed: Path | None = TYPESHED_STDLIB,
        version: Version | None = None,
        platform: str | None = None,
    ) -> SignatureRegistry:
        roots = discover_roots(source, stub_paths, stdlib_allow=stdlib_allow, typeshed=typeshed)
        return cls(roots, version=version, platform=platform)

    def module(self, name: str, *, internal: bool = False) -> StubModule | None:
        """The stub for `name`, or None. `internal` lifts the typeshed allowlist."""
        name = canonical_module(name)
        key = (name, internal)
        if key not in self._modules:
            found = find_stub(name, self.roots, self.version, internal=internal)
            self._modules[key] = (
                None if found is None else load_stub(name, found[1], found[0].kind, self.version, self.platform)
            )
        return self._modules[key]

    def signature(self, module: str, qualname: str) -> StubSignature | None:
        """Declared signature of `module.qualname`, following re-exports."""
        stub = self.module(module)
        return None if stub is None else self._resolve(stub, qualname, hops=0)

    def _resolve(self, stub: StubModule, qualname: str, hops: int) -> StubSignature | None:
        if qualname in stub.functions:
            return stub.functions[qualname]
        if hops >= _MAX_HOPS:
            return None
        head, _, rest = qualname.partition(".")
        target = stub.reexports.get(head)
        if target is not None:
            source = self.module(target[0], internal=True)
            inner = f"{target[1]}.{rest}" if rest else target[1]
            found = None if source is None else self._resolve(source, inner, hops + 1)
            if found is not None:
                return found
            child = self.module(f"{target[0]}.{target[1]}", internal=True) if rest else None
            if child is not None:
                return self._resolve(child, rest, hops + 1)
        for star in stub.star:
            source = self.module(star, internal=True)
            found = None if source is None else self._resolve(source, qualname, hops + 1)
            if found is not None:
                return found
        return None

    def lowering(
        self, module: str, qualname: str, arg_kinds: Sequence[str] | None = None
    ) -> LoweringSignature | Refusal:
        """Lowered form of `module.qualname`.

        With `arg_kinds` (the lowered kinds of the call's positional
        arguments), the first overload whose leading parameters match is
        chosen, as the checker does. Without it, exactly one overload must
        map, or the result is `Refusal("overload-ambiguous")`.
        """
        stub = self.module(module)
        if stub is None:
            return Refusal(module, qualname, "no-stub")
        if stub.ignored:
            return Refusal(module, qualname, "ignored", f"{stub.path}: file-level type: ignore")
        sig = self._resolve(stub, qualname, hops=0)
        if sig is None:
            reason = "incomplete-stub" if stub.incomplete else "not-declared"
            return Refusal(module, qualname, reason)
        if sig.ignored:
            return Refusal(module, qualname, "ignored", f"{sig.path}:{sig.line}")
        lowerer = _Lowerer(self, module, qualname, sig)
        mapped: list[LoweringSignature] = []
        first_refusal: Refusal | None = None
        for variant in sig.overloads:
            result = lowerer.callable(variant, arg_kinds)
            if isinstance(result, Refusal):
                first_refusal = first_refusal or result
                continue
            if arg_kinds is not None and _accepts(result, arg_kinds):
                return result
            mapped.append(result)
        if arg_kinds is not None:
            return first_refusal or Refusal(module, qualname, "no-matching-overload", ", ".join(arg_kinds))
        if len(mapped) == 1:
            return mapped[0]
        if mapped:
            return Refusal(module, qualname, "overload-ambiguous", f"{len(mapped)} overloads map")
        assert first_refusal is not None
        return first_refusal

    def type_alias(self, module: str, name: str, hops: int = 0) -> TypeFacts | None:
        """Expand `name` as a `TypeAlias` visible in stub `module` (declared or imported)."""
        stub = self.module(module, internal=True)
        if stub is None or hops >= _MAX_HOPS:
            return None
        if name in stub.aliases:
            return _parse_type_text(stub.aliases[name])
        target = stub.imports.get(name)
        if target is not None:
            return self.type_alias(target[0], target[1], hops + 1)
        return None


def _accepts(sig: LoweringSignature, arg_kinds: Sequence[str]) -> bool:
    if not sig.min_args <= len(arg_kinds) <= len(sig.params):
        return False
    return all(kind_accepts(param["kind"], kind) for param, kind in zip(sig.params, arg_kinds, strict=False))


def kind_accepts(param_kind: str, arg_kind: str) -> bool:
    """A lowered parameter of `param_kind` takes an argument of `arg_kind` (`buf` takes `bytes`)."""
    return param_kind == arg_kind or (param_kind == "buf" and arg_kind == "bytes")


class _Lowerer:
    """Maps one resolved signature onto lowerer type dicts, expanding stub type aliases."""

    def __init__(self, registry: SignatureRegistry, module: str, qualname: str, sig: StubSignature) -> None:
        self.registry = registry
        self.module = module
        self.qualname = qualname
        self.sig = sig

    def refuse(self, reason: str, detail: str = "") -> Refusal:
        return Refusal(self.module, self.qualname, reason, detail)

    def callable(self, facts: CallableFacts, arg_kinds: Sequence[str] | None = None) -> LoweringSignature | Refusal:
        params: list[dict[str, Any]] = []
        names: list[str] = []
        defaults: list[str | None] = []
        min_args = 0
        for param in facts.parameters:
            if param.kind in {"varargs", "kwargs"}:
                return self.refuse(param.kind, param.name)
            if param.kind == "keyword_only":
                return self.refuse("keyword-only", param.name)
            if param.kind == "keyword_optional":
                continue  # never passed positionally; a call that names it is not covered yet
            want = arg_kinds[len(params)] if arg_kinds is not None and len(params) < len(arg_kinds) else None
            lowered = self.type(param.type, param=True, depth=0, want=want)
            if isinstance(lowered, str):
                return self.refuse(lowered, f"parameter {param.name}: {_repr(param.type)}")
            typ, dropped_none = lowered
            params.append(typ)
            names.append(param.name)
            if param.has_default:
                defaults.append(_default_repr(facts, param, dropped_none))
            elif defaults:
                return self.refuse("default-order", param.name)
            else:
                min_args += 1
        returned = self.type(facts.return_type, param=False, depth=0)
        if isinstance(returned, str):
            return self.refuse(returned, f"return: {_repr(facts.return_type)}")
        return LoweringSignature(
            module=self.module,
            qualname=self.qualname,
            params=tuple(params),
            names=tuple(names),
            returns=returned[0],
            min_args=min_args,
            defaults=tuple(defaults),
            origin=self.sig.origin,
            path=self.sig.path,
            line=self.sig.line,
        )

    def type(
        self, facts: TypeFacts, *, param: bool, depth: int, want: str | None = None
    ) -> tuple[dict[str, Any], bool] | str:
        """`(type dict, dropped_none)` or a refusal reason.

        A parameter union narrows to one lowerable non-None member: the member
        whose kind is `want` (the call's argument kind) when given, else the
        only lowerable member. The callee accepts that member and the lowerer
        only emits the call when the argument has it. A return union is
        refused until Optional widening gives the lowerer a representation.
        """
        kind = facts.kind
        if kind == "any":
            return "any" if facts.runtime_repr else "unannotated"
        if kind == "union":
            if not param:
                return "union-return"
            dropped, choices = self._choices(facts, depth)
            if want is not None:
                choices = [choice for choice in choices if kind_accepts(choice["kind"], want)]
            if len(choices) == 1:
                return choices[0], dropped
            return "union-ambiguous" if choices else "union"
        if kind == "literal" and facts.args:
            return self.type(facts.args[0], param=param, depth=depth)
        if kind in _SCALARS:
            return {"kind": kind}, False
        if kind in _SCALAR_ALIASES:
            return {"kind": _SCALAR_ALIASES[kind]}, False
        if kind == "none":
            return "param-none" if param else ({"kind": "none"}, False)
        if kind in _BUFFER_ALIASES and param:
            return {"kind": "buf"}, False
        if kind in _LIST_HEADS or kind in _SET_HEADS:
            return self._sequence(facts, depth)
        if kind in _DICT_HEADS:
            return self._mapping(facts, depth)
        if kind == "typevar":
            return "typevar"
        if kind == "unknown":
            return "unparsed"
        if not facts.args and depth < _MAX_HOPS and facts.runtime_repr:
            expanded = self.registry.type_alias(self.sig.module, facts.runtime_repr.rsplit(".", 1)[-1])
            if expanded is not None:
                return self.type(expanded, param=param, depth=depth + 1, want=want)
        return f"type:{kind}"

    def _choices(self, facts: TypeFacts, depth: int) -> tuple[bool, list[dict[str, Any]]]:
        """Lowerable members of a parameter union, flattening nested unions and aliases."""
        dropped = False
        out: list[dict[str, Any]] = []
        for member in facts.args:
            if member.kind == "none":
                dropped = True
                continue
            if not member.args and member.runtime_repr and depth < _MAX_HOPS:
                expanded = self.registry.type_alias(self.sig.module, member.runtime_repr.rsplit(".", 1)[-1])
                member = expanded or member
            if member.kind == "union":
                inner_dropped, inner = self._choices(member, depth + 1)
                dropped = dropped or inner_dropped
                out += [choice for choice in inner if choice not in out]
                continue
            lowered = self.type(member, param=True, depth=depth + 1)
            if not isinstance(lowered, str) and lowered[0] not in out:
                out.append(lowered[0])
        return dropped, out

    def _sequence(self, facts: TypeFacts, depth: int) -> tuple[dict[str, Any], bool] | str:
        if len(facts.args) != 1:
            return "generic-arity"
        elem = self.type(facts.args[0], param=False, depth=depth)
        if isinstance(elem, str):
            return elem
        if facts.kind in _SET_HEADS:
            if elem[0]["kind"] not in {"int", "str"}:
                return "set-element"
            return {"kind": "list", "elem": elem[0], "set": True}, False
        if elem[0]["kind"] == "bool":
            return "list-bool"
        return {"kind": "list", "elem": elem[0]}, False

    def _mapping(self, facts: TypeFacts, depth: int) -> tuple[dict[str, Any], bool] | str:
        if len(facts.args) != 2:
            return "generic-arity"
        key = self.type(facts.args[0], param=False, depth=depth)
        value = self.type(facts.args[1], param=False, depth=depth)
        if isinstance(key, str):
            return key
        if isinstance(value, str):
            return value
        if key[0]["kind"] not in {"int", "str"}:
            return "dict-key"
        if value[0]["kind"] == "bool":
            return "dict-bool"
        return {"kind": "dict", "key": key[0], "value": value[0]}, False


def _default_repr(facts: CallableFacts, param: ParameterFacts, dropped_none: bool) -> str | None:
    """Default text in `stdlib_queue` form (`"0"`, `"/"`); None when the stub has no usable value."""
    if param.name not in facts.defaults:
        return None
    value: StaticValue = facts.defaults[param.name]
    if value is None and dropped_none:
        return None  # `x: str | None = None` narrowed to str has no str default
    return value if isinstance(value, str) else repr(value)


def _repr(facts: TypeFacts) -> str:
    return facts.runtime_repr or facts.kind
