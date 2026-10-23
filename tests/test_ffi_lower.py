"""F1a: the `--ffi` flag lowers scalar manifest calls (issue #218)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.ffi_manifest import FfiManifestError, load_manifests
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import ffi_modules, module_status

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks" / "native" / "nativeprobe.toml"

MANIFEST_TEXT = """
[manifest]
module = "demo_math"
ps_abi = 1
format = 1

[[const]]
name = "VERSION"
type = "int"
value = 3

[[fn]]
name = "scale"
py = "(x: int, f: float = 2.0) -> float"
c_symbol = "demo_scale"
abi = "ps_ffi_demo_scale"
"""

BAD_ARITY_TEXT = MANIFEST_TEXT.replace("(x: int, f: float = 2.0) -> float", "(x: int) -> float")


def _lower(source: str, manifests: list) -> dict:
    with ffi_modules(manifests):
        return lower_source(source, "demo.py")


def _parse(text: str) -> object:
    from pyscriptc.ffi_manifest import parse_manifest_text

    return parse_manifest_text(text, path=Path("demo.toml"))


def test_manifest_module_is_lowered_only_when_loaded() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main() -> int:\n    return demo_math.VERSION\n"
    assert module_status("demo_math") is None
    with ffi_modules([manifest]):
        assert module_status("demo_math") == "lowered"
    lowered = _lower(source, [manifest])
    assert lowered["fences"] == []


def test_manifest_module_import_fences_without_ffi() -> None:
    source = "import demo_math\n\ndef main() -> int:\n    return 0\n"
    lowered = _lower(source, [])
    codes = [fence["code"] for fence in lowered["fences"]]
    assert codes == ["PS2002"]
    assert "demo_math" in lowered["fences"][0]["message"]


def test_scalar_call_lowers_to_the_abi_symbol() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main(x: int) -> float:\n    return demo_math.scale(x)\n"
    lowered = _lower(source, [manifest])
    assert lowered["fences"] == []
    call = _first_call(lowered)
    assert call["callee"]["module"] == "demo_math"
    assert call["symbol"] == "ps_ffi_demo_scale"
    assert call["type"] == {"kind": "float"}


def test_default_argument_fills_the_optional_param() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main(x: int) -> float:\n    return demo_math.scale(x, 3.5)\n"
    lowered = _lower(source, [manifest])
    assert lowered["fences"] == []
    assert len(_first_call(lowered)["args"]) == 2


def test_arity_mismatch_is_a_stable_compile_error() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main() -> float:\n    return demo_math.scale(1, 2.0, 3.0)\n"
    lowered = _lower(source, [manifest])
    assert [fence["code"] for fence in lowered["fences"]] == ["PS2003"]


def test_type_mismatch_is_a_stable_compile_error() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main(x: str) -> float:\n    return demo_math.scale(x)\n"
    lowered = _lower(source, [manifest])
    assert [fence["code"] for fence in lowered["fences"]] == ["PS1003"]


def test_unknown_member_is_a_stable_compile_error() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main() -> int:\n    return demo_math.nope()\n"
    lowered = _lower(source, [manifest])
    assert [fence["code"] for fence in lowered["fences"]] == ["PS2001"]


def test_const_lowers_as_a_literal() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "import demo_math\n\ndef main() -> int:\n    return demo_math.VERSION\n"
    lowered = _lower(source, [manifest])
    assert lowered["fences"] == []
    fn = lowered["functions"][0]
    ret = fn["body"][-1]
    assert ret["value"]["kind"] == "const"
    assert ret["value"]["value"] == 3


def test_from_import_binds_manifest_functions_and_consts() -> None:
    manifest = _parse(MANIFEST_TEXT)
    source = "from demo_math import scale, VERSION\n\ndef main() -> float:\n    return scale(VERSION)\n"
    lowered = _lower(source, [manifest])
    assert lowered["fences"] == []
    call = _first_call(lowered)
    assert call["symbol"] == "ps_ffi_demo_scale"


def test_cli_rejects_a_stale_manifest(tmp_path: Path) -> None:
    source = tmp_path / "use.py"
    source.write_text("import demo_math\n\ndef main() -> int:\n    return 0\n", encoding="utf-8")
    manifest = tmp_path / "demo.toml"
    manifest.write_text(MANIFEST_TEXT.replace("ps_abi = 1", "ps_abi = 99"), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(source), "--ffi", str(manifest), "--skip-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "PS4004" in proc.stderr


def test_two_manifests_cannot_claim_one_module(tmp_path: Path) -> None:
    first = tmp_path / "a.toml"
    second = tmp_path / "b.toml"
    first.write_text(MANIFEST_TEXT, encoding="utf-8")
    second.write_text(MANIFEST_TEXT, encoding="utf-8")
    with pytest.raises(FfiManifestError) as exc:
        load_manifests([first, second])
    assert exc.value.code == "PS4007"


def test_manifest_sources_are_linked_after_f2(tmp_path: Path) -> None:
    """A manifest that declares `[[source]]` is accepted and the unit links (#220).

    The F2 gate removed the PS4008 refusal: the manifest reaches the link
    step, which compiles the declared C with the target cc and links it
    statically.
    """
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "demo.c").write_text("int demo_zero(void) { return 0; }\n", encoding="utf-8")
    source = tmp_path / "use.py"
    source.write_text("import demo_math\n\ndef main() -> int:\n    return 0\n", encoding="utf-8")
    manifest = tmp_path / "demo.toml"
    manifest.write_text(MANIFEST_TEXT + '\n[[source]]\npath = "vendor/demo.c"\n', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(source), "--ffi", str(manifest), "--skip-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "PS4008" not in proc.stderr
    assert "PS4009" not in proc.stderr


def _first_call(module: dict):
    for fn in module["functions"]:
        for stmt in fn["body"]:
            value = stmt.get("value")
            if isinstance(value, dict) and value.get("kind") == "call" and "symbol" in value:
                return value
            if isinstance(value, dict) and value.get("kind") == "cast":
                inner = value.get("value", {})
                if inner.get("kind") == "call" and "symbol" in inner:
                    return inner
    raise AssertionError("no abi call found")
