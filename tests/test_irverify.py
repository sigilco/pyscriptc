"""The IR verifier and the LLVM module verifier (tests/test_irverify.py only).

Two verifiers: the structural one over the frozen IR, and the LLVM one over
the emitted text. The structural verifier is checked with hand-built modules so
each failure class is exercised in isolation, and against real lowered
examples so it does not fire on valid IR. The LLVM verifier is checked with a
real emitted module and with a deliberately broken one.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from pyscriptc.irverify import (
    CODE_SCOPE,
    CODE_TERMINATOR,
    CODE_TYPE,
    IRVerifyError,
    verify_module,
    verify_or_raise,
)
from pyscriptc.llvverify import CODE_LLVM_VERIFY, LLVMVerifyError, verify_module_text
from pyscriptc.llvverify import verify_or_raise as verify_ll

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
    )


def span(line: int = 1) -> dict[str, Any]:
    return {"file": "t.py", "line": line, "col": 1, "endLine": line, "endCol": 2}


def const(value: str, kind: str = "int") -> dict[str, Any]:
    return {"kind": "const", "value": value, "type": {"kind": kind}, "span": span()}


def name(ident: str, kind: str = "int") -> dict[str, Any]:
    return {"kind": "name", "id": ident, "type": {"kind": kind}, "span": span()}


def module_with(
    body: list[dict[str, Any]], return_type: str = "int", locals_: list[str] | None = None
) -> dict[str, Any]:
    return {
        "irVersion": 1,
        "subset": "gates-v1",
        "cpython": "3.14.0",
        "sourceFile": "t.py",
        "entry": "f",
        "functions": [
            {
                "name": "f",
                "params": [],
                "returnType": {"kind": return_type},
                "locals": [{"name": n, "type": {"kind": "int"}} for n in (locals_ or [])],
                "body": body,
                "span": span(),
            }
        ],
        "classes": [],
        "fences": [],
    }


def codes(errors: list[IRVerifyError]) -> set[str]:
    return {error.code for error in errors}


# --- the clean module passes -----------------------------------------


def test_valid_module_has_no_violations() -> None:
    module = module_with([{"kind": "return", "value": const("1"), "span": span()}])
    assert verify_module(module) == []
    verify_or_raise(module)


def test_verify_or_raise_raises_the_first_violation() -> None:
    module = module_with(
        [
            {"kind": "assign", "target": "x", "value": const("1"), "span": span()},
            {"kind": "return", "value": name("missing"), "span": span()},
        ],
        locals_=["x"],
    )
    with pytest.raises(IRVerifyError) as caught:
        verify_or_raise(module)
    assert caught.value.code == CODE_SCOPE


# --- types ------------------------------------------------------------


def test_assign_type_mismatch_is_reported() -> None:
    module = module_with(
        [
            {"kind": "assign", "target": "x", "value": const("hi", "str"), "span": span()},
            {"kind": "return", "value": const("1"), "span": span()},
        ],
        locals_=["x"],
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TYPE}
    assert "assigns a str" in errors[0].message


def test_binary_operand_mismatch_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "binary",
                    "op": "+",
                    "left": name("a"),
                    "right": const("b", "str"),
                    "type": {"kind": "int"},
                    "span": span(),
                },
                "span": span(),
            }
        ],
        locals_=["a"],
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TYPE}
    assert "mixes a int and a str" in errors[0].message


def test_compare_operand_mismatch_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "compare",
                    "op": "<",
                    "left": name("a"),
                    "right": const("b", "str"),
                    "type": {"kind": "bool"},
                    "span": span(),
                },
                "span": span(),
            }
        ],
        locals_=["a"],
    )
    assert codes(verify_module(module)) == {CODE_TYPE}


def test_identity_compare_of_different_types_is_allowed() -> None:
    # `x is None` lowers to `x != None`, and `is` compares any two operands.
    module = module_with(
        [
            {
                "kind": "if",
                "cond": {
                    "kind": "compare",
                    "op": "!=",
                    "left": name("a", "str"),
                    "right": const("None", "none"),
                    "type": {"kind": "bool"},
                    "span": span(),
                },
                "then": [],
                "else": [],
                "span": span(),
            },
            {"kind": "return", "value": const("1"), "span": span()},
        ],
        locals_=["a"],
    )
    assert verify_module(module) == []


def test_compare_that_does_not_yield_bool_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "compare",
                    "op": "<",
                    "left": name("a"),
                    "right": const("1"),
                    "type": {"kind": "int"},
                    "span": span(),
                },
                "span": span(),
            }
        ],
        locals_=["a"],
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TYPE}
    assert "not a bool" in errors[0].message


def test_non_bool_condition_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "if",
                "cond": name("a"),
                "then": [{"kind": "return", "value": const("1"), "span": span()}],
                "else": [],
                "span": span(),
            },
            {"kind": "return", "value": const("0"), "span": span()},
        ],
        locals_=["a"],
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TYPE}
    assert "condition is a int, not a bool" in errors[0].message


def test_return_type_mismatch_is_reported() -> None:
    module = module_with([{"kind": "return", "value": const("x", "str"), "span": span()}])
    errors = verify_module(module)
    assert codes(errors) == {CODE_TYPE}
    assert "returns a str" in errors[0].message


def test_void_function_may_return_none() -> None:
    module = module_with([{"kind": "return", "value": const("None", "none"), "span": span()}], return_type="none")
    assert verify_module(module) == []


# --- defined before use -----------------------------------------------


def test_undefined_name_is_reported() -> None:
    module = module_with([{"kind": "return", "value": name("ghost"), "span": span()}])
    errors = verify_module(module)
    assert codes(errors) == {CODE_SCOPE}
    assert "ghost is used but not defined" in errors[0].message


def test_parameter_is_defined() -> None:
    module = module_with([{"kind": "return", "value": name("p"), "span": span()}])
    module["functions"][0]["params"] = [{"name": "p", "type": {"kind": "int"}}]
    assert verify_module(module) == []


def test_env_slot_is_defined() -> None:
    module = module_with([{"kind": "return", "value": name("captured"), "span": span()}])
    module["functions"][0]["env"] = [{"name": "captured", "type": {"kind": "int"}}]
    assert verify_module(module) == []


def test_call_to_a_missing_function_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "call",
                    "callee": {"kind": "function", "name": "nowhere"},
                    "args": [],
                    "type": {"kind": "int"},
                    "span": span(),
                },
                "span": span(),
            }
        ]
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_SCOPE}
    assert "not a function of this module" in errors[0].message


def test_call_to_a_class_method_is_allowed() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "call",
                    "callee": {"kind": "function", "name": "cls.Point.total"},
                    "args": [],
                    "type": {"kind": "int"},
                    "span": span(),
                },
                "span": span(),
            }
        ]
    )
    module["classes"] = [
        {
            "name": "Point",
            "fields": [],
            "methods": [{"name": "total", "params": [], "returnType": {"kind": "int"}, "locals": [], "body": []}],
        }
    ]
    assert codes(verify_module(module)) == {CODE_TERMINATOR}


def test_call_to_a_missing_class_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "return",
                "value": {
                    "kind": "call",
                    "callee": {"kind": "class", "name": "Ghost"},
                    "args": [],
                    "type": {"kind": "int"},
                    "span": span(),
                },
                "span": span(),
            }
        ]
    )
    assert codes(verify_module(module)) == {CODE_SCOPE}


# --- terminators --------------------------------------------------------


def test_function_that_can_fall_off_the_end_is_reported() -> None:
    module = module_with([{"kind": "pass", "span": span()}])
    errors = verify_module(module)
    assert codes(errors) == {CODE_TERMINATOR}
    assert "can reach the end without a return" in errors[0].message


def test_function_that_always_returns_passes() -> None:
    module = module_with([{"kind": "return", "value": const("1"), "span": span()}])
    assert verify_module(module) == []


def test_both_arms_returning_passes() -> None:
    module = module_with(
        [
            {
                "kind": "if",
                "cond": name("a", "bool"),
                "then": [{"kind": "return", "value": const("1"), "span": span()}],
                "else": [{"kind": "return", "value": const("0"), "span": span()}],
                "span": span(),
            }
        ],
        locals_=["a"],
    )
    assert verify_module(module) == []


def test_only_one_arm_returning_is_reported() -> None:
    module = module_with(
        [
            {
                "kind": "if",
                "cond": name("a", "bool"),
                "then": [{"kind": "return", "value": const("1"), "span": span()}],
                "else": [],
                "span": span(),
            }
        ],
        locals_=["a"],
    )
    assert codes(verify_module(module)) == {CODE_TERMINATOR}


def test_statement_after_a_return_is_reported() -> None:
    module = module_with(
        [
            {"kind": "return", "value": const("1"), "span": span()},
            {"kind": "pass", "span": span()},
        ]
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TERMINATOR}
    assert "follows a return" in errors[0].message


def test_statement_after_a_raise_is_reported() -> None:
    module = module_with(
        [
            {"kind": "raise", "exc": "ValueError", "span": span()},
            {"kind": "pass", "span": span()},
        ],
        return_type="none",
    )
    errors = verify_module(module)
    assert codes(errors) == {CODE_TERMINATOR}
    assert "follows a raise" in errors[0].message


def test_void_function_needs_no_terminator() -> None:
    module = module_with([{"kind": "pass", "span": span()}], return_type="none")
    assert verify_module(module) == []


# --- real lowered modules ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["sum_squares.py", "gates_try.py", "invoice.py", "classes.py"],
)
def test_real_examples_verify_clean(name: str) -> None:
    path = EXAMPLES / name
    if not path.is_file():
        pytest.skip(f"{name} is not in this checkout")
    out = Path(os.environ.get("TMPDIR", "/tmp")) / f"irverify-{name}.ir.json"
    proc = run_cli("compile", str(path), "--emit", "ir", "-o", str(out), "--skip-check")
    if proc.returncode == 3:
        pytest.skip(f"{name} is fenced")
    assert proc.returncode == 0, proc.stderr
    module = json.loads(out.read_text())
    errors = verify_module(module)
    assert errors == [], [str(error) for error in errors]


# --- the CLI path ---------------------------------------------------------


def test_debug_runs_the_ir_verifier(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--debug=verify")
    assert proc.returncode == 0, proc.stderr
    assert "[debug] llvmverify: ok" in proc.stderr
    assert "[debug] irverify" not in proc.stderr


def test_debug_stages_show_the_verify_stage(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--debug=all")
    assert proc.returncode == 0, proc.stderr
    assert "[debug] verify" in proc.stderr
    assert "[debug] llvmverify" in proc.stderr


def test_debug_dir_records_the_verification(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    dbg = tmp_path / "dbg"
    proc = run_cli(
        "compile",
        str(EXAMPLES / "sum_squares.py"),
        "--emit",
        "llvm",
        "-o",
        str(out),
        "--debug-dir",
        str(dbg),
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((dbg / "summary.json").read_text())
    assert summary["verify"]["ir"] == []
    assert summary["verify"]["llvm"]["ok"] is True
    assert summary["verify"]["llvm"]["message"] == ""


def test_plain_compile_runs_no_verifier(tmp_path: Path) -> None:
    # Without a debug flag the verifier never runs, so the artifacts of a
    # plain compile stay exactly what they were.
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "verify" not in proc.stderr


def test_debug_does_not_change_the_emitted_text(tmp_path: Path) -> None:
    env = {**ENV, "PYTHONHASHSEED": "0"}
    plain = tmp_path / "plain.ll"
    debug = tmp_path / "debug.ll"
    src = EXAMPLES / "sum_squares.py"
    first = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(src), "--emit", "llvm", "-o", str(plain), "--skip-check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(src),
            "--emit",
            "llvm",
            "-o",
            str(debug),
            "--skip-check",
            "--debug=all",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert first.returncode == second.returncode == 0
    assert plain.read_text() == debug.read_text()


# --- the LLVM verifier ----------------------------------------------------


needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")


@needs_clang
def test_emitted_module_verifies(tmp_path: Path) -> None:
    out = tmp_path / "main.ll"
    proc = run_cli("compile", str(EXAMPLES / "sum_squares.py"), "--emit", "llvm", "-o", str(out), "--skip-check")
    assert proc.returncode == 0, proc.stderr
    result = verify_module_text(out)
    assert result.ok, result.message


@needs_clang
def test_broken_module_does_not_verify(tmp_path: Path) -> None:
    broken = tmp_path / "broken.ll"
    broken.write_text("define i64 @f() {\nentry:\n  br label %nowhere\n}\n", encoding="utf-8")
    result = verify_module_text(broken)
    assert not result.ok
    assert result.message


@needs_clang
def test_broken_module_raises_with_a_code(tmp_path: Path) -> None:
    broken = tmp_path / "broken.ll"
    broken.write_text("define i64 @f() {\nentry:\n  br label %nowhere\n}\n", encoding="utf-8")
    with pytest.raises(LLVMVerifyError) as caught:
        verify_ll(broken)
    assert caught.value.code == CODE_LLVM_VERIFY
    assert "did not verify" in caught.value.message


@needs_clang
def test_debug_dir_reports_a_broken_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken `.ll` in the debug dump is reported as a failed verification
    # with the verifier's own message, not as a link error.
    dbg = tmp_path / "dbg"
    dbg.mkdir()
    (dbg / "out.ll").write_text("define i64 @f() {\nentry:\n  br label %nowhere\n}\n", encoding="utf-8")
    from pyscriptc.llvverify import verify_module_text as verify_text

    result = verify_text(dbg / "out.ll")
    assert not result.ok
    assert result.as_dict()["skipped"] is False


def test_verify_module_text_reports_a_missing_tool(tmp_path: Path) -> None:
    missing = tmp_path / "missing.ll"
    missing.write_text("define i64 @f() {\nentry:\n  ret i64 0\n}\n", encoding="utf-8")
    result = verify_module_text(missing, driver="definitely-not-a-compiler")
    assert not result.ok
    assert result.skipped


# --- the module is not mutated --------------------------------------------


def test_verifier_does_not_mutate_the_module() -> None:
    module = module_with([{"kind": "return", "value": const("1"), "span": span()}])
    before = copy.deepcopy(module)
    verify_module(module)
    assert module == before
