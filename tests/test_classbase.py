# SPDX-License-Identifier: Apache-2.0
"""The class-base census: the kind a refused class base is (issue #1313)."""

from __future__ import annotations

import pytest

from pyscriptc import classbase


def test_the_base_kinds_are_the_census_table_rows() -> None:
    assert classbase.BASE_KINDS == (
        "library",
        "protocol",
        "generic",
        "abc",
        "enum",
        "namedtuple",
        "typeddict",
        "exception",
        "stdlib",
        "other",
    )


@pytest.mark.parametrize(
    ("member", "kind"),
    [
        ("Protocol", "protocol"),
        ("Generic", "generic"),
        ("ABC", "abc"),
        ("ABCMeta", "abc"),
        ("NamedTuple", "namedtuple"),
        ("TypedDict", "typeddict"),
    ],
)
def test_the_typing_members_name_their_own_kind(member: str, kind: str) -> None:
    assert classbase.typing_base_kind(member) == kind


def test_an_unknown_member_names_no_typing_kind() -> None:
    assert classbase.typing_base_kind("BaseModel") is None
    assert classbase.typing_base_kind("") is None


@pytest.mark.parametrize("member", ["Enum", "IntEnum", "StrEnum"])
def test_the_enum_members_name_the_enum_kind(member: str) -> None:
    assert classbase.enum_base_kind(member) == "enum"


def test_a_non_enum_member_names_no_enum_kind() -> None:
    assert classbase.enum_base_kind("BaseModel") is None


def test_the_builtin_types_name_the_stdlib_kind() -> None:
    assert classbase.builtin_base_kind("list") == "stdlib"
    assert classbase.builtin_base_kind("dict") == "stdlib"
    assert classbase.builtin_base_kind("BaseModel") is None


def test_the_standard_library_modules_are_stdlib() -> None:
    assert classbase.is_stdlib_module("threading") is True
    assert classbase.is_stdlib_module("http.server") is True
    assert classbase.is_stdlib_module("sqlalchemy") is False
    assert classbase.is_stdlib_module("") is False


def test_a_base_from_a_library_is_the_library_kind() -> None:
    assert classbase.kind_of(member="BaseModel", module="pydantic") == "library"
    assert classbase.kind_of(member="DeclarativeBase", module="sqlalchemy.orm") == "library"


def test_a_base_from_the_standard_library_is_the_stdlib_kind() -> None:
    assert classbase.kind_of(member="Thread", module="threading") == "stdlib"


def test_a_typing_base_keeps_its_kind_whatever_module_it_came_from() -> None:
    assert classbase.kind_of(member="Protocol", module="typing") == "protocol"
    assert classbase.kind_of(member="Protocol", module="typing_extensions") == "protocol"


def test_an_exception_base_is_the_exception_kind() -> None:
    assert classbase.kind_of(member="ValueError", is_exception=True) == "exception"


def test_an_enum_base_is_the_enum_kind() -> None:
    assert classbase.kind_of(member="StrEnum", is_enum=True) == "enum"
    assert classbase.kind_of(member="StrEnum", module="enum") == "enum"


def test_a_base_that_resolves_to_nothing_is_the_other_kind() -> None:
    assert classbase.kind_of(member="Foo") == "other"
    assert classbase.kind_of() == "other"


def test_a_module_wins_over_the_member_when_the_base_resolves() -> None:
    assert classbase.kind_of(member="Base", module="sqlalchemy") == "library"
