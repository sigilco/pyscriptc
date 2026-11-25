"""The tuple dict key's emission (issue #1368).

A `dict[tuple[str, str | None], int]` stores a tuple as its key. The map's
key cell holds one pointer to a tuple-key record, and the record names each
field's kind and carries the field payloads in a flat buffer. The runtime
hashes and compares that record field-wise (see `ps_tupkey.c`).

The compiler packs the record, because the compiler is the one that knows
each field's static type: the tuple is a synthetic class whose fields the
class table names, so the field kinds and the field offsets are known at
compile time. The packing is therefore straight-line code:

1. `ps_tuple_key_new` allocates the record for the field count and the
   field kinds (the kinds are a constant array the emitter writes once).
2. `ps_tuple_key_data` hands back the payload buffer.
3. Each field is read off the tuple struct and stored into the buffer at
   its offset. A str field stores the 16-byte `PsBuf` prefix, an int field
   its 8 bytes, and an optional field its tag byte and, when the tag says
   the value is there, the arm's payload.

The offsets are the runtime's per-kind sizes, so they are the same numbers
`ps_tk_size` uses: the emitter and the runtime agree because both derive
them from the field kind.
"""

from __future__ import annotations

from typing import Any

from pyscriptc.emit._ir import _field_type, _items, _record
from pyscriptc.errors import IRError

# The field kinds, the codes the runtime's `ps_tupkey.c` spells. The
# emitter writes them into the record's kind array.
_TK_INT = 1
_TK_STR = 2
_TK_FLOAT = 3
_TK_BOOL = 4
_TK_OPT_INT = 5
_TK_OPT_STR = 6
_TK_OPT_FLOAT = 7
_TK_OPT_BOOL = 8

# The payload size of one field, per kind: the runtime's `ps_tk_size`.
_TK_SIZE = {
    _TK_INT: 8,
    _TK_FLOAT: 8,
    _TK_BOOL: 1,
    _TK_STR: 16,
    _TK_OPT_INT: 9,
    _TK_OPT_FLOAT: 9,
    _TK_OPT_STR: 17,
    _TK_OPT_BOOL: 2,
}


def _field_kind(typ: dict[str, Any]) -> int:
    """The tuple-key field kind of one field type.

    The supported field shapes are the scalars (int, float, bool, str) and
    an optional of one of them. An optional field compares by value, so
    `(section, None)` is a key that equals another `(section, None)`.
    """
    kind = typ["kind"]
    if kind in {"int", "float", "bool", "str"}:
        return {
            "int": _TK_INT,
            "float": _TK_FLOAT,
            "bool": _TK_BOOL,
            "str": _TK_STR,
        }[kind]
    if kind == "class" and typ.get("optional"):
        elem = typ.get("elem")
        if elem is None:
            raise IRError("an optional tuple-key field has no arm")
        arm = _field_kind(elem)
        if arm == _TK_INT:
            return _TK_OPT_INT
        if arm == _TK_FLOAT:
            return _TK_OPT_FLOAT
        if arm == _TK_BOOL:
            return _TK_OPT_BOOL
        if arm == _TK_STR:
            return _TK_OPT_STR
        raise IRError("an optional tuple-key field has an unsupported arm")
    raise IRError(f"a tuple key field of kind {kind} is not lowered")


def tuple_key_kinds(fields: list[dict[str, Any]]) -> list[int]:
    """The field kinds of a tuple key, in field order."""
    return [_field_kind(field) for field in fields]


def tuple_key_offsets(kinds: list[int]) -> list[int]:
    """The offset of each field in the record's payload buffer."""
    offsets: list[int] = []
    off = 0
    for kind in kinds:
        offsets.append(off)
        off += _TK_SIZE[kind]
    return offsets


class TupleKeyMixin:
    """The tuple dict key's packing, as a mixin over the LLVM emitter."""

    def _map_key_kind(self, key_type: dict[str, Any]) -> int:
        """The map's key kind for a key type: 1 int, 2 str, 4 tuple.

        The map's key kind decides how the key cell is hashed and compared,
        so the kind is a property of the key's type, not of the call site.
        """
        kind = key_type["kind"]
        if kind == "int":
            return 1
        if kind == "str":
            return 2
        if kind == "class" and self._is_tuple_class(key_type.get("name", "")):
            return 4
        raise IRError(f"a map key of kind {kind} is not lowered")

    def _is_tuple_class(self, class_name: str) -> bool:
        """True when the class is one of the lowerer's synthetic tuples."""
        if not class_name.startswith("tuple_"):
            return False
        info = _record(self.class_index, class_name)
        fields = list(_items(info, "fields"))
        return bool(fields) and all(field.get("name") == f"_{index}" for index, field in enumerate(fields))

    def _stash_map_key(self, key_type: dict[str, Any], value: str) -> str:
        """Stash a map key into the cell the map reads.

        An int or a str key stores its own cell, the way the map's key
        kinds 1 and 2 read it. A tuple key packs the tuple into a
        tuple-key record and stores the record's pointer, the way the map's
        key kind 4 reads it.
        """
        if self._map_key_kind(key_type) == 4:
            slot = self._alloca("ptr")
            rec = self._pack_tuple_key(key_type["name"], value)
            self.line(f"store ptr {rec}, ptr {slot}")
            return slot
        return self._stash_cell(key_type, value)

    def _tuple_key_fields(self, class_name: str) -> list[dict[str, Any]]:
        """The fields of a tuple class: the class table's own list."""
        return list(_items(_record(self.class_index, class_name), "fields"))

    def _tuple_key_kinds_global(self, class_name: str) -> str:
        """The global holding the field kinds of a tuple key shape.

        The kinds are the same for every value of the shape, so they are
        one constant array the record reads. The global's name carries the
        class name, so two shapes never share one.
        """
        symbol = f"@.ps.tupkey.{class_name}"
        for existing in self.globals:
            if existing.startswith(f"{symbol} = "):
                return symbol
        fields = self._tuple_key_fields(class_name)
        kinds = tuple_key_kinds([_field_type(field) for field in fields])
        body = ", ".join(f"i32 {kind}" for kind in kinds)
        self.globals.append(f"{symbol} = private constant [{len(kinds)} x i32] [{body}]")
        return symbol

    def _pack_tuple_key(self, class_name: str, value: str) -> str:
        """Pack a tuple value into a tuple-key record and return its pointer.

        The record is the map's key cell: the map copies the pointer, so
        the record outlives the packing.
        """
        fields = self._tuple_key_fields(class_name)
        kinds = tuple_key_kinds([_field_type(field) for field in fields])
        offsets = tuple_key_offsets(kinds)
        self.needed.add("map")
        self._abi_decl("ps_tuple_key_new", "ptr", ["i64", "ptr"])
        self._abi_decl("ps_tuple_key_data", "ptr", ["ptr"])
        kinds_global = self._tuple_key_kinds_global(class_name)
        rec = self.tmp()
        self.line(f"{rec} = call ptr @ps_tuple_key_new(i64 {len(fields)}, ptr {kinds_global})")
        data = self.tmp()
        self.line(f"{data} = call ptr @ps_tuple_key_data(ptr {rec})")
        for index, field in enumerate(fields):
            self._pack_tuple_key_field(class_name, value, field, kinds[index], offsets[index], data)
        return rec

    def _pack_tuple_key_field(
        self,
        class_name: str,
        value: str,
        field: dict[str, Any],
        kind: int,
        offset: int,
        data: str,
    ) -> None:
        """Store one field of a tuple into the record's payload buffer."""
        name = field["name"]
        ftype = _field_type(field)
        slot = self._field_ptr(class_name, value, self._field(class_name, name))
        if kind == _TK_STR:
            # The str field stores the 16-byte PsBuf prefix: the length and
            # the data pointer, the same shape a str map key stores.
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = load i64, ptr {slot}")
            data_ptr = self.tmp()
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {slot}, i32 0, i32 1")
            self.line(f"{ptr} = load ptr, ptr {data_ptr}")
            self._store_tuple_key_bytes(data, offset, "i64", length)
            self._store_tuple_key_bytes(data, offset + 8, "ptr", ptr)
            return
        if kind in {_TK_INT, _TK_FLOAT}:
            llvm = "i64" if kind == _TK_INT else "double"
            loaded = self._load(llvm, slot)
            if kind == _TK_FLOAT:
                # The float field stores the double's bits, so the payload
                # is eight bytes wide like every other scalar.
                bits = self.tmp()
                self.line(f"{bits} = bitcast double {loaded} to i64")
                loaded = bits
            self._store_tuple_key_bytes(data, offset, "i64", loaded)
            return
        if kind == _TK_BOOL:
            loaded = self._load("i1", slot)
            wide = self.tmp()
            self.line(f"{wide} = zext i1 {loaded} to i64")
            self._store_tuple_key_bytes(data, offset, "i8", wide)
            return
        # The optional field: the tag byte, then the arm's payload when the
        # tag says the value is there. The arm's cell is a heap pointer, so
        # the tag and the payload are read off the cell.
        arm_kind = {
            _TK_OPT_INT: _TK_INT,
            _TK_OPT_FLOAT: _TK_FLOAT,
            _TK_OPT_BOOL: _TK_BOOL,
            _TK_OPT_STR: _TK_STR,
        }[kind]
        cell = self._load("ptr", slot)
        tag = self.tmp()
        self.line(f"{tag} = load i1, ptr {cell}")
        tag_byte = self.tmp()
        self.line(f"{tag_byte} = zext i1 {tag} to i64")
        self._store_tuple_key_bytes(data, offset, "i8", tag_byte)
        arm_class = ftype["name"]
        arm_fields = self._tuple_key_fields(arm_class)
        if not arm_fields:
            raise IRError(f"an optional tuple-key field has no arm class {arm_class}")
        arm_field = arm_fields[1]
        arm_slot = self._field_ptr(arm_class, cell, self._field(arm_class, arm_field["name"]))
        if arm_kind == _TK_STR:
            length = self.tmp()
            ptr = self.tmp()
            self.line(f"{length} = load i64, ptr {arm_slot}")
            data_ptr = self.tmp()
            self.line(f"{data_ptr} = getelementptr %ps.buf, ptr {arm_slot}, i32 0, i32 1")
            self.line(f"{ptr} = load ptr, ptr {data_ptr}")
            self._store_tuple_key_bytes(data, offset + 1, "i64", length)
            self._store_tuple_key_bytes(data, offset + 9, "ptr", ptr)
            return
        if arm_kind == _TK_INT:
            loaded = self._load("i64", arm_slot)
            self._store_tuple_key_bytes(data, offset + 1, "i64", loaded)
            return
        if arm_kind == _TK_FLOAT:
            loaded = self._load("double", arm_slot)
            bits = self.tmp()
            self.line(f"{bits} = bitcast double {loaded} to i64")
            self._store_tuple_key_bytes(data, offset + 1, "i64", bits)
            return
        loaded = self._load("i1", arm_slot)
        wide = self.tmp()
        self.line(f"{wide} = zext i1 {loaded} to i64")
        self._store_tuple_key_bytes(data, offset + 1, "i8", wide)

    def _store_tuple_key_bytes(self, data: str, offset: int, llvm: str, value: str) -> None:
        """Store one field's bytes into the record's payload buffer."""
        slot = self.tmp()
        self.line(f"{slot} = getelementptr i8, ptr {data}, i64 {offset}")
        if llvm == "i8":
            byte = self.tmp()
            self.line(f"{byte} = trunc i64 {value} to i8")
            self.line(f"store i8 {byte}, ptr {slot}")
            return
        self.line(f"store {llvm} {value}, ptr {slot}")
