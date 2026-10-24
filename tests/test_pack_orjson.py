"""The pyscriptc-orjson pack layout and schema under the pack-format spec.

The migration (#411) is mechanical: the pack directory
`packaging/adapters/pyscriptc-orjson/` now carries the #377 layout (pack.toml,
surface/, src/, conformance/, licenses) while the in-tree lowering
(`restlower/_orjson.py`, the vendored stub) stays untouched until the compiler
consumes installed packs (spec section 7 is not implemented yet). The
user-facing dumps/loads behaviour is unchanged by construction, and
tests/test_orjson_pack.py pins the compiled surface.

The conformance runner (#378) does not exist yet; conformance/suite.toml ships
runner-ready and this test validates its shape against the spec's section 10
table.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

from pyscriptc.ffi_manifest import CURRENT_PS_ABI

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "packaging" / "adapters" / "pyscriptc-orjson"

_TRIPLE = re.compile(r"^[a-z0-9_]+-[a-z0-9_]+-[a-z0-9_]+(-[a-z0-9_]+)*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _load_pack_toml() -> dict:
    return tomllib.loads((PACK / "pack.toml").read_text(encoding="utf-8"))


def test_layout_matches_the_spec_skeleton() -> None:
    # The spec's worked skeleton for pyscriptc-orjson (section 1), plus the
    # two [[platform]] triples the worked example carries.
    entries = {path.relative_to(PACK).as_posix() for path in PACK.rglob("*") if path.is_file()}
    assert {
        "pack.toml",
        "surface/orjson/__init__.pyi",
        "src/ps_pack_orjson.c",
        "conformance/suite.toml",
        "LICENSE-APACHE.md",
        "LICENSE-MIT.md",
    } <= entries
    # src/ xor prebuilt/: this pack ships sources only.
    assert not (PACK / "prebuilt").exists()


def test_pack_identity_block() -> None:
    data = _load_pack_toml()
    pack = data["pack"]
    assert pack["name"] == "pyscriptc-orjson"
    assert pack["name"] == PACK.name
    assert _SEMVER.match(pack["version"])
    assert pack["replaces"] == "orjson"
    assert pack["replaces-version"] == ">=3.8,<4"
    assert pack["ps-abi"] == CURRENT_PS_ABI
    assert pack["format"] == 1
    assert {row["name"] for row in data["imports"]} == {"orjson", "ujson", "simplejson"}
    assert {row["triple"] for row in data["platform"]} == {
        "aarch64-apple-darwin",
        "x86_64-unknown-linux-musl",
    }
    for row in data["platform"]:
        assert _TRIPLE.match(row["triple"])
        assert row["wheel-tag"]
    assert data["conformance"]["suite"] == "conformance/suite.toml"


def _ffi_manifest_text(data: dict) -> str:
    """The pack's [ffi] tables in the FFI parser's own schema.

    Spec section 1: the FFI manifest tables embed verbatim under the [ffi]
    prefix. Only the table names carry the prefix, so the shim strips it and
    supplies the [manifest] header the pack's [pack] block already carries.
    """
    ffi = data["ffi"]
    lines = [
        "[manifest]",
        'module = "pyscriptc_orjson"',
        f"ps_abi = {data['pack']['ps-abi']}",
        f"format = {data['pack']['format']}",
    ]
    for table in ("source", "handle", "const", "fn"):
        for row in ffi.get(table, []):
            lines.append("")
            lines.append(f"[[{table}]]")
            for key, value in row.items():
                if isinstance(value, list):
                    rendered = ", ".join(f'"{item}"' for item in value)
                    lines.append(f"{key} = [{rendered}]")
                else:
                    lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n"


def test_ffi_tables_carry_the_surface_signatures() -> None:
    # Spec section 11: the [ffi] tables express dumps/loads as [[ffi.fn]]
    # entries. The py signatures are the surface's own, `Any` and the
    # `str | bytes` union included. The FFI validator's vocabulary has no
    # `Any` (spec section 3 reuses the vocabulary as it is), so the pack
    # records the gap on #377 instead of narrowing the signatures to pass.
    from pyscriptc.ffi_manifest import FfiManifestError, parse_manifest_text

    data = _load_pack_toml()
    rows = {fn["name"]: fn for fn in data["ffi"]["fn"]}
    assert rows["dumps"]["py"] == "(obj: Any, option: int = 0) -> bytes"
    assert rows["loads"]["py"] == "(__obj: str | bytes) -> Any"
    assert rows["dumps"]["c_symbol"] == "ps_json_dumps_opts"
    assert rows["loads"]["c_symbol"] == "ps_json_loads"
    with pytest.raises(FfiManifestError) as caught:
        parse_manifest_text(_ffi_manifest_text(data))
    assert caught.value.code == "PS4006"


def test_ffi_tables_validate_once_the_signatures_are_in_vocabulary() -> None:
    # The structural half of the embedding: with surface-faithful signatures
    # replaced by vocabulary types, the validator accepts every other field
    # of the [ffi] embedding unchanged (PS4001-PS4009).
    from pyscriptc.ffi_manifest import parse_manifest_text

    data = _load_pack_toml()
    for fn in data["ffi"]["fn"]:
        fn["py"] = "(obj: bytes) -> bytes"
    manifest = parse_manifest_text(_ffi_manifest_text(data))
    assert {fn.name for fn in manifest.functions} == {"dumps", "loads"}
    assert manifest.ps_abi == CURRENT_PS_ABI
    assert manifest.format == 1


def test_ffi_tables_refuse_an_unsupported_abi_pin() -> None:
    # The ps-abi pin is load-bearing: a pack built against another epoch is
    # refused (PS4004), never linked.
    from pyscriptc.ffi_manifest import FfiManifestError, parse_manifest_text

    data = _load_pack_toml()
    data["pack"]["ps-abi"] = CURRENT_PS_ABI + 1
    with pytest.raises(FfiManifestError) as caught:
        parse_manifest_text(_ffi_manifest_text(data))
    assert caught.value.code == "PS4004"


def test_surface_is_the_vendored_stub_byte_identical() -> None:
    # Spec section 11: the surface is byte-identical to the vendored stub the
    # compiler still serves, so user-facing behaviour is unchanged.
    vendored = (ROOT / "src" / "pyscriptc" / "_pyright_stubs" / "orjson" / "__init__.pyi").read_text(encoding="utf-8")
    surface = (PACK / "surface" / "orjson" / "__init__.pyi").read_text(encoding="utf-8")
    assert surface.split("\n\n", 1)[1] == vendored.split("\n\n", 1)[1]
    tree = ast.parse(surface)
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {"dumps", "loads"}


def test_thunk_defines_every_abi_symbol() -> None:
    data = _load_pack_toml()
    source = (PACK / "src" / "ps_pack_orjson.c").read_text(encoding="utf-8")
    for fn in data["ffi"]["fn"]:
        assert re.search(rf"\b{fn['abi']}\(", source), fn["abi"]
        assert fn["c_symbol"] in {"ps_json_dumps_opts", "ps_json_loads"}


def test_conformance_suite_shape() -> None:
    # The spec's section 10 table: import, [[case]] rows, expected, compare.
    suite = tomllib.loads((PACK / "conformance" / "suite.toml").read_text(encoding="utf-8"))
    assert suite["import"] == "orjson"
    names = set()
    for case in suite["case"]:
        assert {"name", "expr", "expected", "compare"} <= set(case), case["name"]
        assert case["compare"] in {"stdout", "exception", "exit"}
        assert case["name"] not in names
        names.add(case["name"])
    # The sequences are the ones the compiled surface is pinned by.
    assert {"dumps_compact", "dumps_sorted", "dumps_indent", "loads_str", "round_trip"} <= names
    fenced = {case["name"] for case in suite["case"] if case["compare"] == "exception"}
    assert fenced == {"default_stays_fenced", "unknown_option_stays_fenced", "fragment_stays_fenced"}


@pytest.mark.parametrize("name", ["orjson", "ujson", "simplejson"])
def test_native_packages_registry_points_at_the_pack(name: str) -> None:
    registry = json.loads((ROOT / "src" / "pyscriptc" / "native_packages.json").read_text(encoding="utf-8"))
    row = next(entry for entry in registry["packages"] if entry["dist"] == name)
    assert row["pack"] == "pyscriptc-orjson"
