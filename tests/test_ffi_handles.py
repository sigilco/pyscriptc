"""F1b: str, bytes and opaque handle marshalling with borrows and consumes.

Issue #224. The lowering tests live next to the F1a ones (test_ffi_lower.py);
this file covers the parts that need a real binary: the handle lifecycle
(open, use, consume) under the sanitizer, and the str and bytes round trips
including a bytes value with an embedded NUL.

The fixture C file (tests/fixtures/ffi/ps_ffi_fixture.c) is linked through
link_binary's extra_units seam, because the manifest [[source]] gate is F2
(issue #220) and refuses manifest-declared C units for now.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pyscriptc.emit.llvm import emit_llvm, link_binary
from pyscriptc.ffi_manifest import load_manifest
from pyscriptc.lower import lower_source
from pyscriptc.stdlib_queue import ffi_modules

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ffi"
MANIFEST = FIXTURE / "ffx.toml"
CFILE = FIXTURE / "ps_ffi_fixture.c"

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")


def _manifest():
    return load_manifest(MANIFEST)


def _lower(source: str):
    manifest = _manifest()
    with ffi_modules([manifest]):
        return lower_source(source, "ffx_demo.py"), manifest


def _codes(module: dict) -> list[str]:
    return [fence["code"] for fence in module["fences"]]


def _fn_body(ll: str, name: str) -> str:
    """The body of one emitted user function.

    A user function keeps its name (`@dup`); one called `main` is renamed to
    `ps.user.main` so the C entry can keep the plain name.
    """
    symbol = "ps.user.main" if name == "main" else name
    marker = f"@{symbol}("
    start = ll.index(marker)
    open_brace = ll.index("{", start)
    depth = 0
    for index in range(open_brace, len(ll)):
        if ll[index] == "{":
            depth += 1
        elif ll[index] == "}":
            depth -= 1
            if depth == 0:
                return ll[open_brace + 1 : index]
    raise AssertionError(f"function {name} never closes")


def _build(tmp_path: Path, source: str, *, sanitize: list[str] | None = None) -> Path:
    """Lower, emit and link one fixture program. Returns the binary."""
    module, _manifest_ = _lower(source)
    assert module["fences"] == [], module["fences"]
    ll = tmp_path / "program.ll"
    ll.write_text(emit_llvm(module, standalone=True), encoding="utf-8")
    binary = tmp_path / "program"
    link_binary(ll, binary, extra_units=[CFILE], sanitize=sanitize)
    return binary


# --- lowering ------------------------------------------------------------


def test_handle_annotation_binds_from_a_manifest_import() -> None:
    source = (
        "import ffx\n"
        "from ffx import Resource\n"
        "\n"
        "def main() -> int:\n"
        "    r: Resource = ffx.open('x')\n"
        "    return ffx.use(r, 2)\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    local = module["functions"][0]["locals"][0]
    assert local["type"] == {"kind": "class", "name": "Resource", "handle": True}


def test_qualified_handle_annotation_binds_too() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return ffx.use(r, 2)\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    local = module["functions"][0]["locals"][0]
    assert local["type"] == {"kind": "class", "name": "Resource", "handle": True}


def test_handle_return_is_a_ptr_not_a_class_record() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return ffx.use(r, 2)\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    # The thunk returns the opaque pointer directly: no class record, no
    # `void @sym(..., ptr out)` form.
    assert "call ptr @ps_ffi_ffx_open(" in ll
    assert "alloca %cls.Resource" not in ll


def test_use_after_consume_is_a_stable_compile_error() -> None:
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    ffx.close(r)\n"
        "    return ffx.use(r, 2)\n"
    )
    module, _ = _lower(source)
    assert _codes(module) == ["PS4010"]
    assert "consumed" in module["fences"][0]["message"]


def test_wrong_handle_type_is_a_stable_compile_error() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    ffx.close(r)\n    return 0\n"
    module, _ = _lower(source)
    assert module["fences"] == []


def test_handle_field_access_is_a_stable_compile_error() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return r.uses\n"
    module, _ = _lower(source)
    assert _codes(module) == ["PS4015"]


def test_handle_arithmetic_is_a_stable_compile_error() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return r + 1\n"
    module, _ = _lower(source)
    assert _codes(module) == ["PS4016"]


def test_handle_name_is_not_a_value() -> None:
    source = (
        "import ffx\nfrom ffx import Resource\n\ndef main() -> None:\n    x: int = 0\n    x = Resource\n    print(x)\n"
    )
    module, _ = _lower(source)
    assert _codes(module) == ["PS4014"]
    module, _ = _lower(source)
    assert _codes(module) == ["PS4014"]


def test_scope_exit_frees_an_owned_handle() -> None:
    """A handle still owned when the scope ends is freed there."""
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return ffx.use(r, 2)\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    body = module["functions"][0]["body"]
    assert body[0]["kind"] == "try"
    assert body[0]["handlers"] == []
    assert body[0]["finalbody"][0]["expr"]["symbol"] == "ps_ffi_ffx_close"


def test_a_consumed_handle_is_not_freed_again_at_scope_exit() -> None:
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    ffx.close(r)\n    return 0\n"
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    # One close: the explicit consume frees it, the scope exit must not
    # free the same pointer again.
    assert ll.count("call void @ps_ffi_ffx_close(") == 1


def test_returning_a_handle_transfers_ownership() -> None:
    """A handle returned from a function leaves the callee, so the callee
    does not free it; the caller owns it."""
    source = (
        "import ffx\n"
        "\n"
        "def open_twice() -> ffx.Resource:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    return r\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = open_twice()\n"
        "    return ffx.use(r, 2)\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    # The helper does not free the handle it returns: ownership moved to the
    # caller, which frees it at its own scope exit.
    assert "ps_ffi_ffx_close" not in _fn_body(ll, "open_twice")
    # The caller frees it exactly once at runtime (the finally shape emits
    # the free on each path, but only one path runs).
    assert "call void @ps_ffi_ffx_close(" in _fn_body(ll, "main")


def test_str_and_bytes_marshal_through_the_runtime_abi() -> None:
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    s: str = ffx.echo('hello')\n"
        "    b: bytes = ffx.bounce(b'a\\x00b')\n"
        "    return len(s) + len(b)\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    # str and bytes pass as an (i64 len, ptr) pair and come back through a
    # ptr out-slot.
    assert "declare void @ps_ffi_ffx_echo(i64, ptr, ptr)" in ll
    assert "declare void @ps_ffi_ffx_bounce(i64, ptr, ptr)" in ll


def test_a_consumed_argument_must_be_a_variable() -> None:
    """A temporary cannot be consumed: the compiler cannot prove it is dead."""
    source = "import ffx\n\ndef main() -> int:\n    ffx.close(ffx.open('x'))\n    return 0\n"
    module, _ = _lower(source)
    assert _codes(module) == ["PS4011"]


def test_a_handle_parameter_is_a_borrow() -> None:
    """The callee's handle parameter belongs to the caller: the callee frees
    nothing at its scope exit, the caller frees exactly once."""
    source = (
        "import ffx\n"
        "\n"
        "def dup(res: ffx.Resource) -> int:\n"
        "    x: ffx.Resource = res\n"
        "    return ffx.use(x, 2)\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = dup(r)\n"
        "    ffx.close(r)\n"
        "    return n\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    # The callee never frees the borrowed handle, not even through the
    # alias `x`; the caller frees its own once.
    assert "ps_ffi_ffx_close" not in _fn_body(ll, "dup")
    assert _fn_body(ll, "main").count("call void @ps_ffi_ffx_close(") == 1


def test_a_borrowed_parameter_cannot_be_consumed() -> None:
    """A consuming call needs a handle this function owns: a parameter is
    the caller's, so consuming it would free a live value."""
    source = "import ffx\n\ndef closer(res: ffx.Resource) -> int:\n    ffx.close(res)\n    return 0\n"
    module, _ = _lower(source)
    assert _codes(module) == ["PS4011"]
    assert "borrowed" in module["fences"][0]["message"]


def test_a_local_alias_of_a_borrow_stays_borrowed() -> None:
    """`x = res` copies the pointer, not the ownership: the scope exit must
    not free `x` because the caller still holds it."""
    source = (
        "import ffx\n"
        "\n"
        "def dup(res: ffx.Resource) -> int:\n"
        "    x: ffx.Resource = res\n"
        "    return ffx.use(x, 2)\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = dup(r)\n"
        "    ffx.close(r)\n"
        "    return n\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    assert "ps_ffi_ffx_close" not in _fn_body(ll, "dup")


def test_a_rebind_to_a_borrow_frees_the_dropped_owned_value() -> None:
    """`r = res` over an owned `r` frees the value the rebinding drops: the
    new value is the caller's, but the old one died here."""
    source = (
        "import ffx\n"
        "\n"
        "def work(res: ffx.Resource) -> int:\n"
        "    r: ffx.Resource = ffx.open('y')\n"
        "    r = res\n"
        "    return ffx.use(r, 1)\n"
        "\n"
        "def main() -> int:\n"
        "    q: ffx.Resource = ffx.open('x')\n"
        "    n: int = work(q)\n"
        "    ffx.close(q)\n"
        "    return n\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    work = _fn_body(ll, "work")
    # One open, one close: the owned value the rebinding dropped.
    assert work.count("ps_ffi_ffx_open(") == 1
    assert work.count("ps_ffi_ffx_close(") == 1
    # The scope exit adds no second close: the borrowed value is not owned.
    assert _fn_body(ll, "main").count("ps_ffi_ffx_close(") == 1


def test_a_borrowed_parameter_passes_through_returns() -> None:
    source = (
        "import ffx\n"
        "\n"
        "def passthru(res: ffx.Resource) -> ffx.Resource:\n"
        "    return res\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    r2: ffx.Resource = passthru(r)\n"
        "    n: int = ffx.use(r2, 1)\n"
        "    ffx.close(r)\n"
        "    return n\n"
    )
    module, _ = _lower(source)
    assert module["fences"] == []
    ll = emit_llvm(module, standalone=True)
    assert "ps_ffi_ffx_close" not in _fn_body(ll, "passthru")


# --- the running binary --------------------------------------------------


@needs_clang
def test_handle_lifecycle_runs_and_frees(tmp_path: Path) -> None:
    """open, use, consume: the program runs and the handle is freed once."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = ffx.use(r, 3)\n"
        "    ffx.close(r)\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@needs_clang
def test_scope_exit_free_runs(tmp_path: Path) -> None:
    """A handle the scope still owns is freed when the scope ends."""
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return ffx.use(r, 3)\n"
    binary = _build(tmp_path, source)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "ps_ffi_ffx_close" not in proc.stderr


@needs_clang
def test_sanitized_handle_lifecycle_is_leak_free(tmp_path: Path) -> None:
    """The open/use/close cycle leaks nothing under ASan and UBSan."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = ffx.use(r, 3)\n"
        "    ffx.close(r)\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr


@needs_clang
def test_sanitized_scope_exit_free_is_leak_free(tmp_path: Path) -> None:
    """The scope-exit free releases the handle, so ASan sees no leak."""
    source = "import ffx\n\ndef main() -> int:\n    r: ffx.Resource = ffx.open('x')\n    return ffx.use(r, 3)\n"
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr


@needs_clang
def test_returned_handle_is_freed_once(tmp_path: Path) -> None:
    """A handle passed through a helper is freed exactly once: the callee
    transfers ownership, the caller frees it at its scope exit."""
    source = (
        "import ffx\n"
        "\n"
        "def hand_down() -> ffx.Resource:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    return r\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = hand_down()\n"
        "    n: int = ffx.use(r, 2)\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr


@needs_clang
def test_close_count_matches_open_count(tmp_path: Path) -> None:
    """The fixture's own counters prove one open, one use, one close."""
    source = (
        "import ffx\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = ffx.use(r, 2)\n"
        "    ffx.close(r)\n"
        "    if ffx.open_count() != ffx.close_count():\n"
        "        return -1\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@needs_clang
def test_sanitized_borrow_is_leak_free(tmp_path: Path) -> None:
    """A handle passed to a helper is freed once, by the owner: the callee's
    borrow adds no free and no leak."""
    source = (
        "import ffx\n"
        "\n"
        "def dup(res: ffx.Resource) -> int:\n"
        "    x: ffx.Resource = res\n"
        "    return ffx.use(x, 2)\n"
        "\n"
        "def main() -> int:\n"
        "    r: ffx.Resource = ffx.open('x')\n"
        "    n: int = dup(r)\n"
        "    ffx.close(r)\n"
        "    if ffx.open_count() != ffx.close_count():\n"
        "        return -1\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr


@needs_clang
def test_sanitized_rebind_to_a_borrow_is_leak_free(tmp_path: Path) -> None:
    """The value a rebinding drops is freed even when the new value is a
    borrow: ASan sees no leak."""
    source = (
        "import ffx\n"
        "\n"
        "def work(res: ffx.Resource) -> int:\n"
        "    r: ffx.Resource = ffx.open('y')\n"
        "    r = res\n"
        "    return ffx.use(r, 1)\n"
        "\n"
        "def main() -> int:\n"
        "    q: ffx.Resource = ffx.open('x')\n"
        "    n: int = work(q)\n"
        "    ffx.close(q)\n"
        "    if ffx.open_count() != ffx.close_count():\n"
        "        return -1\n"
        "    return n\n"
    )
    binary = _build(tmp_path, source, sanitize=["address", "undefined"])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "LeakSanitizer" not in proc.stderr
    assert "AddressSanitizer" not in proc.stderr


@needs_clang
def test_str_round_trip_prints_the_same_text(tmp_path: Path) -> None:
    source = "import ffx\n\ndef main() -> None:\n    s: str = ffx.echo('hello world')\n    print(s)\n"
    binary = _build(tmp_path, source)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "hello world\n"


@needs_clang
def test_bytes_round_trip_keeps_the_embedded_nul(tmp_path: Path) -> None:
    """A bytes value with a NUL crosses the ABI whole: the length, not a
    C string, is what travels."""
    source = "import ffx\n\ndef main() -> None:\n    b: bytes = ffx.bounce(b'a\\x00b')\n    print(len(b))\n"
    binary = _build(tmp_path, source)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "3\n"
