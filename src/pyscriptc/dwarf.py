"""DWARF debug metadata for the LLVM backend (`--debug-info`).

The emitter prints the same instructions with and without the flag; the only
difference is that the instruction lines carry a `!dbg` attachment and the
module tail carries the named-metadata block. A debug build is a superset: the
same instructions in the same order with locations added. The line table comes
from the IR spans, so `lldb` and `gdb` show the Python file and line of a
frame instead of the generated helper names.

Metadata numbering follows the LLVM textual IR rules: `!0` and `!1` are the
module flags, `!2` is the compile unit (every `DISubprogram` names it as its
scope), and the collected records follow from `!3`. A record may reference a
later record, so the file of the compile unit is allocated before the unit
itself.
"""

from __future__ import annotations

import os
from typing import Any

# The CPython language code (0x001e). The debug info describes the source the
# user wrote, not the generated C-flavoured IR, so a debugger renders frames as
# "Python" rather than falling back to C.
DW_LANG_Python = 0x001E

# DWARF tags for the subset of DIEs the backend emits.
DW_TAG_member = 0x0D
DW_TAG_pointer_type = 0x0F
DW_TAG_structure_type = 0x13

# Attribute encodings for `DIBasicType`.
DW_ATE_address = 0x01
DW_ATE_boolean = 0x02
DW_ATE_float = 0x04
DW_ATE_signed = 0x05

# The compile unit is always `!2`: `!0` and `!1` are the module flags, and
# every `DISubprogram` names the unit as its scope.
_FLAGS = (0, 1)
_CU = 2
_FIRST_RECORD = 3


def _span_file(span: dict[str, Any]) -> str:
    file = span.get("file") if isinstance(span, dict) else None
    return file if isinstance(file, str) and file else "<unknown>.py"


def _span_line(span: dict[str, Any]) -> int:
    line = span.get("line", 1) if isinstance(span, dict) else 1
    return line if isinstance(line, int) and line > 0 else 1


def _span_col(span: dict[str, Any]) -> int:
    col = span.get("col", 1) if isinstance(span, dict) else 1
    return col if isinstance(col, int) and col > 0 else 1


class DwarfEmitter:
    """Collects `!DI*` records while the LLVM body is printed, then renders them.

    One instance per module. The module tail is the named metadata block
    (`llvm.module.flags`, `llvm.dbg.cu`) followed by every collected record in
    number order.
    """

    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self._records: dict[int, str] = {}
        self._next = _FIRST_RECORD
        self._cache: dict[tuple, int] = {}
        self._scopes: list[int] = []
        self.current_subprogram: int | None = None
        self.current_name: str | None = None
        # The compile unit names the file of the module's source, so the record
        # exists from the start and every later reference to it is backward.
        self.primary_file = self.file(source_file)

    # -- record helpers -----------------------------------------------------

    def _add(self, key: tuple, text: str) -> int:
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        ref = self._next
        self._next += 1
        self._records[ref] = text
        self._cache[key] = ref
        return ref

    def file(self, path: str) -> int:
        absolute = os.path.abspath(path)
        directory, name = os.path.split(absolute)
        return self._add(("file", absolute), f'!DIFile(filename: "{name}", directory: "{directory}")')

    def _basic_type(self, kind: str) -> str:
        if kind == "float":
            return '!DIBasicType(name: "double", size: 64, encoding: DW_ATE_float)'
        if kind == "bool":
            return '!DIBasicType(name: "bool", size: 8, encoding: DW_ATE_boolean)'
        return '!DIBasicType(name: "int", size: 64, encoding: DW_ATE_signed)'

    def type_ref(self, kind: str) -> int:
        """The `!DI*` type for an IR type kind.

        `str`/`bytes` are a two-field struct in the runtime, so they get a
        composite type; the object-like kinds are a pointer to the runtime
        object; `none` is the empty type.
        """
        if kind in {"str", "bytes"}:
            return self._add(("type", kind), self._struct_type(kind))
        if kind in {"int", "float", "bool"}:
            return self._add(("type", kind), self._basic_type(kind))
        if kind == "none":
            return self._add(("type", kind), "!{}")
        return self._add(("type", kind), self._pointer_type(kind))

    def _pointer_type(self, kind: str) -> str:
        base = self.type_ref("int")
        return f'!DIDerivedType(tag: DW_TAG_pointer_type, name: "{kind}", baseType: !{base}, size: 64)'

    def _struct_type(self, kind: str) -> str:
        """The `str`/`bytes` struct: a length and a data pointer."""
        base = self.type_ref("int")
        length = self._add(
            ("member", kind, "length"),
            f'!DIDerivedType(tag: DW_TAG_member, name: "length", baseType: !{base}, size: 64)',
        )
        data = self._add(
            ("member", kind, "data"),
            f'!DIDerivedType(tag: DW_TAG_member, name: "data", baseType: !{self.type_ref("object")}, size: 64)',
        )
        return (
            f'!DICompositeType(tag: DW_TAG_structure_type, name: "{kind}", '
            f"file: !{self.primary_file}, line: 0, size: 128, elements: !{{!{length}, !{data}}})"
        )

    def subroutine_type(self, params: list[dict[str, Any]], ret: dict[str, Any], env: bool = False) -> int:
        """The `DISubroutineType` of a function signature.

        The types list is the return type first and then the parameters, which
        is the order LLVM expects. `env` marks the env-first functions (nested
        defs and lambdas), whose leading `ptr %env` is part of the signature.
        """
        parts = [f"!{self.type_ref(ret['kind'])}" if ret.get("kind") != "none" else "null"]
        if env:
            parts.append(f"!{self.type_ref('env')}")
        parts.extend(f"!{self.type_ref(param['type']['kind'])}" for param in params)
        key = ("func", env, tuple(param["type"].get("kind") for param in params), ret.get("kind"))
        return self._add(key, f"!DISubroutineType(types: !{{{', '.join(parts)}}})")

    def subprogram(
        self,
        name: str,
        linkage: str,
        span: dict[str, Any],
        params: list[dict[str, Any]],
        ret: dict[str, Any],
        env: bool = False,
    ) -> int:
        file = self.file(_span_file(span))
        type_ref = self.subroutine_type(params, ret, env)
        line = _span_line(span)
        return self._add(
            ("sub", linkage),
            f'distinct !DISubprogram(name: "{name}", linkageName: "{linkage}", scope: !{_CU}, file: !{file}, '
            f"line: {line}, type: !{type_ref}, unit: !{_CU}, scopeLine: {line}, "
            "flags: DIFlagPrototyped | DIFlagAllCallsDescribed, spFlags: DISPFlagDefinition)",
        )

    def location(self, span: dict[str, Any], scope: int | None = None) -> int:
        line = _span_line(span)
        col = _span_col(span)
        scope = self.current_subprogram if scope is None else scope
        key = ("loc", _span_file(span), line, col, scope)
        return self._add(
            key,
            f"!DILocation(line: {line}, column: {col}, scope: !{scope})",
        )

    def variable(self, name: str, span: dict[str, Any], kind: str, arg: int) -> int:
        file = self.file(_span_file(span))
        line = _span_line(span)
        arg_part = f"arg: {arg}, " if arg else ""
        key = ("var", name, arg, line, file, kind)
        return self._add(
            key,
            f'!DILocalVariable(name: "{name}", {arg_part}scope: !{self.current_subprogram}, '
            f"file: !{file}, line: {line}, type: !{self.type_ref(kind)})",
        )

    # -- attachment API -----------------------------------------------------

    def dbg(self, span: dict[str, Any] | None) -> str:
        """The `!dbg` fragment to append to an instruction, or '' when off."""
        if span is None or self.current_subprogram is None:
            return ""
        return f", !dbg !{self.location(span)}"

    def enter_function(
        self,
        name: str,
        linkage: str,
        span: dict[str, Any],
        params: list[dict[str, Any]],
        ret: dict[str, Any],
        env: bool = False,
    ) -> int:
        """Make `linkage` the current scope: its statements carry its locations."""
        ref = self.subprogram(name, linkage, span, params, ret, env)
        self._scopes.append(ref)
        self.current_subprogram = ref
        self.current_name = name
        return ref

    def exit_function(self) -> None:
        """Restore the enclosing scope (an outlined `try` body, the caller)."""
        if self._scopes:
            self._scopes.pop()
        self.current_subprogram = self._scopes[-1] if self._scopes else None
        self.current_name = None

    # -- rendering ----------------------------------------------------------

    def render(self) -> list[str]:
        """The module-tail lines: named metadata and every record in order."""
        self._records[_CU] = (
            f"distinct !DICompileUnit(language: DW_LANG_Python, file: !{self.primary_file}, "
            'producer: "pyscriptc", isOptimized: false, runtimeVersion: 0, emissionKind: FullDebug)'
        )
        lines = [
            "",
            "!llvm.module.flags = !{!0, !1}",
            f"!llvm.dbg.cu = !{{!{_CU}}}",
            "",
            '!0 = !{i32 2, !"Debug Info Version", i32 3}',
            '!1 = !{i32 2, !"Dwarf Version", i32 4}',
        ]
        for ref in sorted(self._records):
            lines.append(f"!{ref} = {self._records[ref]}")
        return lines
