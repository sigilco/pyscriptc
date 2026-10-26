"""Issue #626: a decorator expression never crashes the compiler.

`@click.command()` decorates a function with a decorator expression whose
value is not a lowered callable. The lowerer used to read `params` out of a
type that has no such key and died with `KeyError: 'params'`. Now the
definition refuses at a named fence (`PS2006`), and under
`--fallback=cpython` it rides the island floor instead.

The crash is the regression this file guards: every case must name a
diagnostic, never a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_gates import run_cli, write_case

# A decorator whose value is an object: the shape that used to crash.
_CLASS_DECORATOR = "\n".join(
    [
        "class Command:",
        "    def __call__(self, fn: int) -> int:",
        "        return fn",
        "",
        "def command() -> Command:",
        "    return Command()",
        "",
    ]
)

CASES: dict[str, list[str]] = {
    "module_level.py": [
        *_CLASS_DECORATOR.splitlines(),
        "@command()",
        "def probe() -> int:",
        "    return 1",
        "",
    ],
    "nested.py": [
        *_CLASS_DECORATOR.splitlines(),
        "def probe() -> int:",
        "    @command()",
        "    def inner() -> int:",
        "        return 1",
        "    return inner()",
        "",
    ],
}


def test_decorator_expression_refuses_with_a_named_code(tmp_path: Path) -> None:
    for name, lines in CASES.items():
        path = write_case(tmp_path, name, "\n".join(lines))
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert "PS2006" in got.stderr, f"{name}\n{got.stderr}"
        assert "KeyError" not in got.stderr, f"{name}\n{got.stderr}"
        assert "Traceback" not in got.stderr, f"{name}\n{got.stderr}"


def test_decorator_expression_routes_to_the_island(tmp_path: Path) -> None:
    """Under `--fallback=cpython` the decorated definition plans island and
    the run matches CPython."""
    source = "\n".join(
        [
            "def command():",
            "    def decorator(fn):",
            "        return fn",
            "    return decorator",
            "",
            "@command()",
            "def probe() -> int:",
            "    return 1",
            "",
            "def caller() -> int:",
            "    return probe()",
            "",
            "caller()",
            "",
        ]
    )
    path = write_case(tmp_path, "island.py", source)
    report = tmp_path / "report.json"
    got = run_cli("run", "--fallback=cpython", "--report", str(report), str(path))
    assert got.returncode == 0, got.stderr
    assert "Traceback" not in got.stderr, got.stderr
    planes = json.loads(report.read_text(encoding="utf-8"))
    assert planes["planeMap"]["functions"]["probe"] == "island"


def test_decorator_expression_no_traceback_under_fallback(tmp_path: Path) -> None:
    """A class-typed decorator expression compiles under the fallback tier
    without crashing the lowering pass (the original #626 symptom)."""
    path = write_case(
        tmp_path,
        "class_dec.py",
        "\n".join([*_CLASS_DECORATOR.splitlines(), "@command()", "def probe() -> int:", "    return 1", ""]),
    )
    got = run_cli("run", "--fallback=cpython", str(path))
    assert "KeyError" not in got.stderr, got.stderr
    assert "Traceback" not in got.stderr, got.stderr
