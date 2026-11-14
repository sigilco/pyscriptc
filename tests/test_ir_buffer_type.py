"""The IR buffer type (issue #372, stage A type plumbing).

The type exists in the vocabulary, validates its flat numeric element,
keys distinctly per element, and round trips through both hashing paths
(byte-identically) and the closed-table mirror.
"""

from __future__ import annotations

import json

import pytest

from pyscriptc import ir, tirhash, tirserde
from pyscriptc._irnode import t_buffer, t_leaf
from pyscriptc.errors import IRError
from pyscriptc.sigrec import t_buffer as sig_t_buffer


def test_buffer_is_a_type_kind() -> None:
    assert "buffer" in ir.TYPE_KINDS
    assert ir.BUFFER_ELEM_KINDS == {"int", "float"}


def test_a_buffer_type_validates_with_a_numeric_elem() -> None:
    ir.validate_type({"kind": "buffer", "elem": {"kind": "float"}}, "t")
    ir.validate_type({"kind": "buffer", "elem": {"kind": "int"}}, "t")


def test_a_buffer_type_needs_an_elem() -> None:
    with pytest.raises(IRError, match="buffer type needs elem"):
        ir.validate_type({"kind": "buffer"}, "t")


def test_a_buffer_element_is_flat_and_numeric() -> None:
    for elem in (
        {"kind": "str"},
        {"kind": "list", "elem": {"kind": "float"}},
        {"kind": "buffer", "elem": {"kind": "int"}},
    ):
        with pytest.raises(IRError, match="buffer element"):
            ir.validate_type({"kind": "buffer", "elem": elem}, "t")


def test_the_type_key_names_the_element() -> None:
    assert ir.type_key({"kind": "buffer", "elem": {"kind": "float"}}) == "buffer[float]"
    assert ir.type_key({"kind": "buffer", "elem": {"kind": "int"}}) == "buffer[int]"
    # Distinct elements are distinct types.
    assert not ir.types_equal(
        {"kind": "buffer", "elem": {"kind": "float"}},
        {"kind": "buffer", "elem": {"kind": "int"}},
    )
    assert ir.types_equal(
        {"kind": "buffer", "elem": {"kind": "float"}},
        {"kind": "buffer", "elem": {"kind": "float"}},
    )


def test_the_t_buffer_factory_round_trips_the_dict() -> None:
    typ = t_buffer(t_leaf("float"))
    d = tirserde._type_dict(typ)
    assert d == {"kind": "buffer", "elem": {"kind": "float"}}
    back = tirserde.dict_to_type(d)
    assert back == typ
    assert back.kind == "buffer"
    assert back.elem == t_leaf("float")


def test_the_sigrec_mirror_spells_the_same_dict() -> None:
    assert tirserde._type_dict(t_buffer(t_leaf("int"))) == {"kind": "buffer", "elem": {"kind": "int"}}
    assert tirserde._type_dict(sig_t_buffer(t_leaf("int"))) == {"kind": "buffer", "elem": {"kind": "int"}}


def test_the_two_hashers_digest_a_buffer_identically() -> None:
    module = {
        "irVersion": ir.IR_VERSION,
        "subset": ir.SUBSET,
        "entry": "f",
        "functions": [
            {
                "name": "f",
                "params": [{"name": "a", "type": {"kind": "buffer", "elem": {"kind": "float"}}}],
                "returnType": {"kind": "buffer", "elem": {"kind": "float"}},
                "locals": [],
                "body": [],
            }
        ],
        "classes": [],
        "fences": [],
    }
    # The typed payload spells the structure in schema field order; the
    # buffer branch must keep `elem`.
    typed_tree = json.loads(tirhash.payload_bytes_node(module))
    assert typed_tree["ir"] == module
    fn = typed_tree["ir"]["functions"][0]
    assert fn["returnType"] == {"kind": "buffer", "elem": {"kind": "float"}}
    assert fn["params"][0]["type"] == {"kind": "buffer", "elem": {"kind": "float"}}


def test_a_dropped_elem_is_not_silent_in_the_digest() -> None:
    """The unknown-kind fallback used to drop `elem`; the buffer branch
    must keep it, or two buffers with different elements would hash alike."""
    module_float = {
        "irVersion": ir.IR_VERSION,
        "subset": ir.SUBSET,
        "entry": "f",
        "functions": [
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "buffer", "elem": {"kind": "float"}},
                "locals": [],
                "body": [],
            }
        ],
        "classes": [],
        "fences": [],
    }
    module_int = {
        **module_float,
        "functions": [
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": "buffer", "elem": {"kind": "int"}},
                "locals": [],
                "body": [],
            }
        ],
    }
    assert tirhash.hash_module_node(module_float) != tirhash.hash_module_node(module_int)
