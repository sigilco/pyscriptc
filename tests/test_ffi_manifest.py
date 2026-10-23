"""The FFI manifest parser and validator (`pyscriptc.ffi_manifest`).

The validator is the F0 gate of the C library lane: it parses a typed
extern manifest and refuses anything the lowering could not honour. Every
failure has a stable code, so the tests below match on the code and not on
the prose.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pyscriptc.ffi_manifest import (
    CURRENT_PS_ABI,
    SUPPORTED_FORMATS,
    FfiManifest,
    FfiManifestError,
    HandleRef,
    load_manifest,
    load_manifests,
    parse_manifest_text,
    type_ir,
)

HEADER = """
[manifest]
module = "pyscriptc_sqlite"
ps_abi = 1
format = 1
"""


def manifest(body: str = "") -> str:
    """A manifest with the standard header plus a body."""
    return textwrap.dedent(HEADER + textwrap.dedent(body))


def write(tmp_path: Path, text: str, name: str = "ffi.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def fails(text: str, code: str, **kwargs: object) -> FfiManifestError:
    """Assert that a manifest text fails with a given code."""
    with pytest.raises(FfiManifestError) as excinfo:
        parse_manifest_text(text, **kwargs)  # type: ignore[arg-type]
    assert excinfo.value.code == code, f"expected {code}, got {excinfo.value.code}: {excinfo.value}"
    return excinfo.value


# --- the happy path -------------------------------------------------------


def test_header_only_manifest_is_valid() -> None:
    parsed = parse_manifest_text(manifest())
    assert parsed.module == "pyscriptc_sqlite"
    assert parsed.ps_abi == CURRENT_PS_ABI
    assert parsed.format == 1
    assert parsed.functions == ()
    assert parsed.handles == ()
    assert parsed.consts == ()
    assert parsed.sources == ()


def test_full_pilot_manifest_parses() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[source]]
            path = "vendor/sqlite3.c"
            [[source]]
            path = "ps_ffi_sqlite.c"

            [[handle]]
            name = "Connection"
            c_type = "sqlite3 *"
            frees = "sqlite3_close_v2"
            [[handle]]
            name = "Statement"
            c_type = "sqlite3_stmt *"
            frees = "sqlite3_finalize"

            [[const]]
            name = "OK"
            type = "int"
            value = 0
            [[const]]
            name = "ROW"
            type = "int"
            value = 100

            [[fn]]
            name = "connect"
            py = "(path: str) -> Connection"
            c_symbol = "sqlite3_open_v2"
            abi = "ps_ffi_sqlite_connect"

            [[fn]]
            name = "execute"
            py = "(db: Connection, sql: str) -> int"
            c_symbol = "sqlite3_exec"
            abi = "ps_ffi_sqlite_execute"
            borrows = ["db"]

            [[fn]]
            name = "close"
            py = "(db: Connection) -> None"
            c_symbol = "sqlite3_close_v2"
            abi = "ps_ffi_sqlite_close"
            consumes = ["db"]
            """
        )
    )
    assert parsed.module == "pyscriptc_sqlite"
    assert [fn.name for fn in parsed.functions] == ["connect", "execute", "close"]
    assert [handle.name for handle in parsed.handles] == ["Connection", "Statement"]
    assert [const.name for const in parsed.consts] == ["OK", "ROW"]
    assert parsed.const("ROW").value == 100
    assert parsed.handle("Statement").c_type == "sqlite3_stmt *"
    assert parsed.function("execute").borrows == (HandleRef(param="db", handle="Connection"),)
    assert parsed.function("close").consumes == (HandleRef(param="db", handle="Connection"),)
    assert parsed.function("connect").returns == "Connection"
    assert parsed.function("close").returns == "None"


def test_lookup_helpers_return_none_for_unknown_names() -> None:
    parsed = parse_manifest_text(manifest())
    assert parsed.function("nope") is None
    assert parsed.handle("nope") is None
    assert parsed.const("nope") is None


def test_source_paths_resolve_relative_to_the_manifest(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        manifest(
            """
            [[source]]
            path = "vendor/sqlite3.c"
            [[source]]
            objects = ["vendor/prebuilt.o", "vendor/other.o"]
            """
        ),
    )
    parsed = load_manifest(path)
    assert parsed.unit_paths() == [
        tmp_path / "vendor" / "sqlite3.c",
        tmp_path / "vendor" / "prebuilt.o",
        tmp_path / "vendor" / "other.o",
    ]


def test_signature_parameters_and_defaults() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[handle]]
            name = "Connection"
            c_type = "sqlite3 *"
            frees = "sqlite3_close_v2"

            [[fn]]
            name = "query"
            py = "(db: Connection, sql: str, limit: int = 10) -> list"
            c_symbol = "sqlite3_prepare_v2"
            abi = "ps_ffi_sqlite_query"
            """
        )
    )
    fn = parsed.function("query")
    assert fn.arity == 3
    assert fn.required_arity == 2
    assert [param.name for param in fn.params] == ["db", "sql", "limit"]
    assert [param.type for param in fn.params] == ["Connection", "str", "int"]
    assert fn.params[0].required is True
    assert fn.params[2].required is False
    assert fn.params[2].default == "10"


def test_signature_without_return_means_none() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[fn]]
            name = "flush"
            py = "(fd: int)"
            c_symbol = "ps_flush"
            abi = "ps_ffi_flush"
            """
        )
    )
    assert parsed.function("flush").returns == "None"


def test_empty_parameter_list() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[fn]]
            name = "now"
            py = "() -> int"
            c_symbol = "time"
            abi = "ps_ffi_now"
            """
        )
    )
    assert parsed.function("now").arity == 0


def test_ownership_annotations_accept_handle_names() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[handle]]
            name = "Connection"
            c_type = "sqlite3 *"
            frees = "sqlite3_close_v2"

            [[fn]]
            name = "execute"
            py = "(db: Connection, sql: str) -> int"
            c_symbol = "sqlite3_exec"
            abi = "ps_ffi_sqlite_execute"
            borrows = ["Connection"]
            """
        )
    )
    assert parsed.function("execute").borrows == (HandleRef(param="db", handle="Connection"),)


def test_const_value_types_are_preserved() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[const]]
            name = "OK"
            type = "int"
            value = 0
            [[const]]
            name = "RATIO"
            type = "float"
            value = 0.5
            [[const]]
            name = "NAME"
            type = "str"
            value = "sqlite"
            """
        )
    )
    assert parsed.const("OK").value == 0
    assert parsed.const("RATIO").value == 0.5
    assert parsed.const("NAME").value == "sqlite"


def test_integer_const_accepts_an_integral_float() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[const]]
            name = "RATIO"
            type = "float"
            value = 2
            """
        )
    )
    assert parsed.const("RATIO").value == 2.0


def test_load_manifests_reads_several_files(tmp_path: Path) -> None:
    first = write(tmp_path, manifest(), "a.toml")
    second = write(tmp_path, manifest().replace("pyscriptc_sqlite", "pyscriptc_zlib"), "b.toml")
    manifests = load_manifests([first, second])
    assert [m.module for m in manifests] == ["pyscriptc_sqlite", "pyscriptc_zlib"]


# --- the pins -------------------------------------------------------------


def test_missing_ps_abi_pin_is_refused() -> None:
    error = fails('[manifest]\nmodule = "x"\nformat = 1\n', "PS4004")
    assert "ps_abi" in error.message


def test_stale_ps_abi_pin_is_refused() -> None:
    error = fails(
        '[manifest]\nmodule = "x"\nps_abi = 99\nformat = 1\n',
        "PS4004",
    )
    assert "99" in error.message


def test_non_integer_ps_abi_pin_is_refused() -> None:
    fails('[manifest]\nmodule = "x"\nps_abi = "1"\nformat = 1\n', "PS4004")


def test_missing_format_pin_is_refused() -> None:
    fails('[manifest]\nmodule = "x"\nps_abi = 1\n', "PS4003")


def test_unknown_format_is_refused() -> None:
    error = fails('[manifest]\nmodule = "x"\nps_abi = 1\nformat = 7\n', "PS4003")
    assert "7" in error.message


def test_non_integer_format_is_refused() -> None:
    fails('[manifest]\nmodule = "x"\nps_abi = 1\nformat = "1"\n', "PS4003")


def test_supported_formats_are_a_frozen_set() -> None:
    assert SUPPORTED_FORMATS == frozenset({1})


def test_the_compiler_ps_abi_is_one() -> None:
    assert CURRENT_PS_ABI == 1


def test_a_manifest_may_pin_a_different_epoch_when_the_compiler_does() -> None:
    parsed = parse_manifest_text(
        '[manifest]\nmodule = "x"\nps_abi = 4\nformat = 1\n',
        ps_abi=4,
    )
    assert parsed.ps_abi == 4


# --- the header -----------------------------------------------------------


def test_missing_manifest_section_is_refused() -> None:
    error = fails('[source]\npath = "a.c"\n', "PS4002")
    assert "[manifest]" in error.message


def test_manifest_section_that_is_not_a_table_is_refused() -> None:
    fails("manifest = 3\n", "PS4002")


def test_a_duplicate_manifest_table_is_a_toml_error() -> None:
    # TOML rejects the duplicate table before the validator sees it.
    fails('[manifest]\nmodule = "x"\nps_abi = 1\nformat = 1\n[manifest]\n', "PS4001")


def test_missing_module_is_refused() -> None:
    fails("[manifest]\nps_abi = 1\nformat = 1\n", "PS4005")


def test_module_must_be_a_string() -> None:
    fails("[manifest]\nmodule = 3\nps_abi = 1\nformat = 1\n", "PS4005")


def test_module_must_be_a_dotted_identifier() -> None:
    fails('[manifest]\nmodule = "not a module"\nps_abi = 1\nformat = 1\n', "PS4002")
    fails('[manifest]\nmodule = "1bad"\nps_abi = 1\nformat = 1\n', "PS4002")


def test_dotted_module_is_accepted() -> None:
    parsed = parse_manifest_text('[manifest]\nmodule = "pkg.mod"\nps_abi = 1\nformat = 1\n')
    assert parsed.module == "pkg.mod"


def test_unknown_header_key_is_refused() -> None:
    error = fails('[manifest]\nmodule = "x"\nps_abi = 1\nformat = 1\nnope = 1\n', "PS4005")
    assert "nope" in error.message


# --- malformed TOML -------------------------------------------------------


def test_invalid_toml_is_refused() -> None:
    error = fails("[manifest\nmodule = ", "PS4001")
    assert "TOML" in error.message


def test_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FfiManifestError) as excinfo:
        load_manifest(tmp_path / "nope.toml")
    assert excinfo.value.code == "PS4001"


def test_non_utf8_manifest_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ffi.toml"
    path.write_bytes(b'[manifest]\nmodule = "\xff\xfe"\n')
    with pytest.raises(FfiManifestError) as excinfo:
        load_manifest(path)
    assert excinfo.value.code == "PS4001"


# --- sources --------------------------------------------------------------


def test_source_without_path_or_objects_is_refused() -> None:
    fails(manifest("[[source]]\n"), "PS4005")


def test_source_with_both_path_and_objects_is_refused() -> None:
    fails(manifest('[[source]]\npath = "a.c"\nobjects = ["b.o"]\n'), "PS4005")


def test_source_with_an_absolute_path_is_refused() -> None:
    error = fails(manifest('[[source]]\npath = "/etc/passwd"\n'), "PS4005")
    assert "relative" in error.message


def test_source_with_a_parent_escape_is_refused() -> None:
    fails(manifest('[[source]]\npath = "../outside.c"\n'), "PS4005")


def test_source_with_an_escaping_object_is_refused() -> None:
    fails(manifest('[[source]]\nobjects = ["../outside.o"]\n'), "PS4005")


def test_unknown_source_key_is_refused() -> None:
    fails(manifest('[[source]]\npath = "a.c"\nflags = ["-O2"]\n'), "PS4005")


def test_source_path_must_be_a_string() -> None:
    fails(manifest("[[source]]\npath = 3\n"), "PS4005")


# --- link flags ------------------------------------------------------------


def test_manifest_without_link_flags_has_none() -> None:
    parsed = parse_manifest_text(manifest())
    assert parsed.libs == ()
    assert parsed.lib_dirs == ()
    assert parsed.link_flags() == []


def test_manifest_link_flags_are_read() -> None:
    parsed = parse_manifest_text(manifest('libs = ["z"]\nlib_dirs = ["/opt/ffi/lib"]\n'))
    assert parsed.libs == ("z",)
    assert parsed.lib_dirs == ("/opt/ffi/lib",)
    assert parsed.link_flags() == ["-L/opt/ffi/lib", "-lz"]


def test_a_lib_entry_with_the_dash_is_refused() -> None:
    """The `libs` entry is the bare soname: `-lz` would become `--lz`."""
    error = fails(manifest('libs = ["-lz"]\n'), "PS4005")
    assert "bare library name" in error.message


def test_a_lib_entry_with_the_file_name_is_refused() -> None:
    fails(manifest('libs = ["libz.so"]\n'), "PS4005")


def test_a_lib_dir_entry_with_the_dash_is_refused() -> None:
    error = fails(manifest('lib_dirs = ["-L/opt"]\n'), "PS4005")
    assert "bare directory path" in error.message


def test_libs_must_be_a_list_of_strings() -> None:
    fails(manifest("libs = 3\n"), "PS4005")
    fails(manifest('libs = ["z", 3]\n'), "PS4005")


def test_lib_dirs_must_be_a_list_of_strings() -> None:
    fails(manifest("lib_dirs = 3\n"), "PS4005")
    fails(manifest('lib_dirs = ["/opt", 3]\n'), "PS4005")


# --- the unit split --------------------------------------------------------


def test_the_units_split_into_compile_and_link(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        manifest(
            """
            [[source]]
            path = "vendor/sqlite3.c"
            [[source]]
            objects = ["vendor/prebuilt.a", "vendor/other.o"]
            """
        ),
    )
    parsed = load_manifest(path)
    assert parsed.compile_units() == [tmp_path / "vendor" / "sqlite3.c"]
    assert parsed.link_units() == [
        tmp_path / "vendor" / "prebuilt.a",
        tmp_path / "vendor" / "other.o",
    ]


# --- handles --------------------------------------------------------------


def test_handle_without_a_c_type_is_refused() -> None:
    fails(manifest('[[handle]]\nname = "Connection"\n'), "PS4005")


def test_handle_name_must_be_an_identifier() -> None:
    fails(manifest('[[handle]]\nname = "not an ident"\nc_type = "sqlite3 *"\n'), "PS4005")


def test_handle_c_type_must_look_like_a_c_type() -> None:
    fails(manifest('[[handle]]\nname = "C"\nc_type = "sqlite3 *;"\n'), "PS4005")


def test_handle_frees_must_be_a_c_identifier() -> None:
    fails(
        manifest('[[handle]]\nname = "C"\nc_type = "sqlite3 *"\nfrees = "sqlite3_close_v2 (x)"\n'),
        "PS4005",
    )


def test_duplicate_handle_is_refused() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[handle]]
            name = "C"
            c_type = "sqlite3_stmt *"
            """
        ),
        "PS4007",
    )
    assert "C" in error.message


def test_handle_frees_may_be_omitted() -> None:
    parsed = parse_manifest_text(manifest('[[handle]]\nname = "C"\nc_type = "sqlite3 *"\n'))
    assert parsed.handle("C").frees == ""


# --- constants ------------------------------------------------------------


def test_const_without_a_value_is_refused() -> None:
    fails(manifest('[[const]]\nname = "OK"\ntype = "int"\n'), "PS4005")


def test_const_type_must_be_known() -> None:
    error = fails(manifest('[[const]]\nname = "OK"\ntype = "bytes"\nvalue = 1\n'), "PS4005")
    assert "bytes" in error.message


def test_int_const_needs_an_integer_value() -> None:
    fails(manifest('[[const]]\nname = "OK"\ntype = "int"\nvalue = "zero"\n'), "PS4005")


def test_int_const_refuses_a_boolean_value() -> None:
    fails(manifest('[[const]]\nname = "OK"\ntype = "int"\nvalue = true\n'), "PS4005")


def test_float_const_needs_a_numeric_value() -> None:
    fails(manifest('[[const]]\nname = "R"\ntype = "float"\nvalue = "x"\n'), "PS4005")


def test_str_const_needs_a_string_value() -> None:
    fails(manifest('[[const]]\nname = "S"\ntype = "str"\nvalue = 1\n'), "PS4005")


def test_duplicate_const_is_refused() -> None:
    fails(
        manifest(
            """
            [[const]]
            name = "OK"
            type = "int"
            value = 0
            [[const]]
            name = "OK"
            type = "int"
            value = 1
            """
        ),
        "PS4007",
    )


def test_unknown_const_key_is_refused() -> None:
    fails(manifest('[[const]]\nname = "OK"\ntype = "int"\nvalue = 0\ndoc = "x"\n'), "PS4005")


# --- functions ------------------------------------------------------------


def test_function_without_a_py_signature_is_refused() -> None:
    fails(manifest('[[fn]]\nname = "f"\nc_symbol = "f"\nabi = "ps_f"\n'), "PS4005")


def test_function_without_an_abi_thunk_is_refused() -> None:
    fails(manifest('[[fn]]\nname = "f"\npy = "() -> int"\nc_symbol = "f"\n'), "PS4005")


def test_function_without_a_c_symbol_is_refused() -> None:
    fails(manifest('[[fn]]\nname = "f"\npy = "() -> int"\nabi = "ps_f"\n'), "PS4005")


def test_abi_thunk_must_be_a_c_identifier() -> None:
    fails(manifest('[[fn]]\nname = "f"\npy = "() -> int"\nc_symbol = "f"\nabi = "ps f"\n'), "PS4005")


def test_function_name_must_be_an_identifier() -> None:
    fails(manifest('[[fn]]\nname = "not an ident"\npy = "() -> int"\nc_symbol = "f"\nabi = "ps_f"\n'), "PS4005")


def test_duplicate_function_is_refused() -> None:
    fails(
        manifest(
            """
            [[fn]]
            name = "f"
            py = "() -> int"
            c_symbol = "f"
            abi = "ps_f"
            [[fn]]
            name = "f"
            py = "() -> int"
            c_symbol = "g"
            abi = "ps_g"
            """
        ),
        "PS4007",
    )


def test_unknown_function_key_is_refused() -> None:
    fails(manifest('[[fn]]\nname = "f"\npy = "() -> int"\nc_symbol = "f"\nabi = "ps_f"\nfast = true\n'), "PS4005")


def test_c_symbol_may_document_a_composite_body() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[fn]]
            name = "query"
            py = "() -> list"
            c_symbol = "sqlite3_prepare_v2 + sqlite3_step + sqlite3_finalize"
            abi = "ps_ffi_sqlite_query"
            """
        )
    )
    assert parsed.function("query").c_symbol == "sqlite3_prepare_v2 + sqlite3_step + sqlite3_finalize"


def test_borrows_must_be_a_list_of_strings() -> None:
    fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[fn]]
            name = "f"
            py = "(db: C) -> int"
            c_symbol = "f"
            abi = "ps_f"
            borrows = "db"
            """
        ),
        "PS4005",
    )


# --- signatures and the type vocabulary ----------------------------------


def test_signature_must_have_parentheses() -> None:
    error = fails(
        manifest('[[fn]]\nname = "f"\npy = "path: str -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    assert "signature" in error.message


def test_parameter_must_be_name_colon_type() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "(path) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )


def test_duplicate_parameter_name_is_refused() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "(a: int, a: str) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4007",
    )


def test_unknown_type_is_refused() -> None:
    error = fails(
        manifest('[[fn]]\nname = "f"\npy = "(x: MyType) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    assert "MyType" in error.message


def test_dynamic_types_are_refused() -> None:
    for name in ("Any", "object", "NoneType"):
        fails(
            manifest(f'[[fn]]\nname = "f"\npy = "(x: {name}) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
            "PS4006",
        )


def test_typed_containers_carry_the_annotation() -> None:
    """A typed container annotation maps onto the subset's elem/key/value IR
    forms, so a returned list or dict is a regular subset value (issue #236)."""
    parsed = parse_manifest_text(
        manifest(
            '[[fn]]\nname = "f"\npy = "() -> list[str]"\nc_symbol = "f"\nabi = "ps_f"\n'
            '[[fn]]\nname = "g"\npy = "() -> dict[str, int]"\nc_symbol = "g"\nabi = "ps_g"\n'
            '[[fn]]\nname = "h"\npy = "() -> list[dict[str, str]]"\nc_symbol = "h"\nabi = "ps_h"\n'
        )
    )
    assert type_ir(parsed.function("f").returns) == {"kind": "list", "elem": {"kind": "str"}}
    assert type_ir(parsed.function("g").returns) == {
        "kind": "dict",
        "key": {"kind": "str"},
        "value": {"kind": "int"},
    }
    assert type_ir(parsed.function("h").returns) == {
        "kind": "list",
        "elem": {"kind": "dict", "key": {"kind": "str"}, "value": {"kind": "str"}},
    }


def test_typed_containers_stay_one_level() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> list[list[int]]"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> dict[str, dict[str, int]]"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> list[str, int]"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> dict[str]"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> list[dict]"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )
    # A handle is an opaque token: a container of handles has no subset
    # representation (the elem would be a bare ptr the subset cannot touch).
    fails(
        manifest(
            '[[fn]]\nname = "f"\npy = "() -> list[Connection]"\nc_symbol = "f"\nabi = "ps_f"\n'
            '[[handle]]\nname = "Connection"\nc_type = "conn *"\n'
        ),
        "PS4006",
    )


def test_none_is_refused_as_a_parameter_type() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "(x: None) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )


def test_scalar_and_container_types_are_accepted() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[fn]]
            name = "f"
            py = "(a: int, b: float, c: bool, d: str, e: bytes, g: list, h: dict) -> bytes"
            c_symbol = "f"
            abi = "ps_f"
            """
        )
    )
    fn = parsed.function("f")
    assert [param.type for param in fn.params] == ["int", "float", "bool", "str", "bytes", "list", "dict"]
    assert fn.returns == "bytes"


def test_a_handle_type_is_accepted() -> None:
    parsed = parse_manifest_text(
        manifest(
            """
            [[handle]]
            name = "Connection"
            c_type = "sqlite3 *"
            [[fn]]
            name = "f"
            py = "(db: Connection) -> Connection"
            c_symbol = "f"
            abi = "ps_f"
            """
        )
    )
    assert parsed.function("f").returns == "Connection"


def test_a_handle_that_is_never_declared_is_refused() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "(db: Connection) -> int"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )


def test_a_handle_used_only_as_a_return_type_must_be_declared() -> None:
    fails(
        manifest('[[fn]]\nname = "f"\npy = "() -> Connection"\nc_symbol = "f"\nabi = "ps_f"\n'),
        "PS4006",
    )


# --- ownership ------------------------------------------------------------


def test_borrows_must_name_a_handle_or_a_handle_parameter() -> None:
    fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[fn]]
            name = "f"
            py = "(db: C) -> int"
            c_symbol = "f"
            abi = "ps_f"
            borrows = ["nope"]
            """
        ),
        "PS4008",
    )


def test_borrows_must_not_name_a_non_handle_parameter() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[fn]]
            name = "f"
            py = "(db: C, sql: str) -> int"
            c_symbol = "f"
            abi = "ps_f"
            borrows = ["sql"]
            """
        ),
        "PS4008",
    )
    assert "sql" in error.message


def test_a_handle_cannot_be_both_borrowed_and_consumed() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            frees = "c_close"
            [[fn]]
            name = "f"
            py = "(db: C) -> int"
            c_symbol = "f"
            abi = "ps_f"
            borrows = ["db"]
            consumes = ["db"]
            """
        ),
        "PS4008",
    )
    assert "db" in error.message


def test_ownership_is_per_argument_not_per_handle_type() -> None:
    """Two arguments of one handle type are distinct ownership references."""
    parsed = parse_manifest_text(
        manifest(
            """
            [[handle]]
            name = "Connection"
            c_type = "sqlite3 *"
            frees = "sqlite3_close_v2"

            [[fn]]
            name = "move"
            py = "(src: Connection, dst: Connection) -> int"
            c_symbol = "sqlite3_backup"
            abi = "ps_ffi_sqlite_move"
            borrows = ["src"]
            consumes = ["dst"]
            """
        )
    )
    fn = parsed.function("move")
    assert fn.borrows == (HandleRef(param="src", handle="Connection"),)
    assert fn.consumes == (HandleRef(param="dst", handle="Connection"),)


def test_a_handle_type_with_no_carrier_is_refused() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            frees = "c_close"
            [[fn]]
            name = "f"
            py = "(x: int) -> int"
            c_symbol = "f"
            abi = "ps_f"
            borrows = ["C"]
            """
        ),
        "PS4008",
    )
    assert "C" in error.message


def test_a_consumed_handle_must_declare_a_frees_symbol() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[fn]]
            name = "f"
            py = "(db: C) -> int"
            c_symbol = "f"
            abi = "ps_f"
            consumes = ["db"]
            """
        ),
        "PS4008",
    )
    assert "frees" in error.message


# --- namespace ------------------------------------------------------------


def test_a_handle_and_a_function_cannot_share_a_name() -> None:
    error = fails(
        manifest(
            """
            [[handle]]
            name = "C"
            c_type = "sqlite3 *"
            [[fn]]
            name = "C"
            py = "() -> int"
            c_symbol = "f"
            abi = "ps_f"
            """
        ),
        "PS4007",
    )
    assert "C" in error.message


def test_a_const_and_a_function_cannot_share_a_name() -> None:
    fails(
        manifest(
            """
            [[const]]
            name = "OK"
            type = "int"
            value = 0
            [[fn]]
            name = "OK"
            py = "() -> int"
            c_symbol = "f"
            abi = "ps_f"
            """
        ),
        "PS4007",
    )


# --- module claims --------------------------------------------------------


def test_two_manifests_claiming_one_module_are_refused(tmp_path: Path) -> None:
    first = write(tmp_path, manifest(), "a.toml")
    second = write(tmp_path, manifest(), "b.toml")
    with pytest.raises(FfiManifestError) as excinfo:
        load_manifests([first, second])
    assert excinfo.value.code == "PS4007"
    assert "pyscriptc_sqlite" in excinfo.value.message


def test_two_manifests_claiming_different_modules_are_accepted(tmp_path: Path) -> None:
    first = write(tmp_path, manifest(), "a.toml")
    second = write(tmp_path, manifest().replace("pyscriptc_sqlite", "pyscriptc_zlib"), "b.toml")
    assert len(load_manifests([first, second])) == 2


# --- error surface --------------------------------------------------------


def test_the_error_carries_a_code_and_a_message() -> None:
    error = fails(manifest('[[handle]]\nname = "C"\n'), "PS4005")
    assert error.code == "PS4005"
    assert error.message
    assert str(error).startswith("PS4005: ")


def test_the_error_names_the_file_and_the_section(tmp_path: Path) -> None:
    path = write(tmp_path, manifest('[[handle]]\nname = "C"\n'), "ffi.toml")
    with pytest.raises(FfiManifestError) as excinfo:
        load_manifest(path)
    error = excinfo.value
    assert error.path == path
    assert error.where.startswith("[[handle]]")
    assert str(path) in error.message


def test_the_error_is_a_spike_error_so_the_cli_can_catch_it() -> None:
    from pyscriptc.errors import SpikeError

    with pytest.raises(SpikeError):
        parse_manifest_text("[manifest]\n")


def test_a_manifest_without_a_path_reports_the_where_section() -> None:
    error = fails(manifest('[[const]]\nname = "OK"\ntype = "int"\n'), "PS4005")
    assert error.path is None
    assert "[[const]]" in error.message


# --- the manifest object --------------------------------------------------


def test_the_manifest_is_a_frozen_dataclass() -> None:
    import dataclasses

    parsed = parse_manifest_text(manifest())
    assert isinstance(parsed, FfiManifest)
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.module = "other"  # type: ignore[misc]


def test_handle_names_are_exposed_as_a_set() -> None:
    parsed = parse_manifest_text(manifest('[[handle]]\nname = "C"\nc_type = "sqlite3 *"\n'))
    assert parsed.handle_names == frozenset({"C"})
