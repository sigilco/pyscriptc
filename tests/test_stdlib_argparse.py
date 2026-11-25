"""argparse: the script subset of the command-line parser.

One `ArgumentParser`, a few `add_argument` calls and `parse_args`. The
parser keeps its options in a list of encoded specs, `parse_args` walks
the process argv, and the namespace carries one field per option.

The subset reads the option spellings, `dest`, `action`, `const`,
`default`, `type`, `required` and `help`. `type` is `str`, `int` or `float`.

Each probe compiles to a binary, runs it with the same argv the CPython
oracle gets, and diffs the stdout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

from pyscriptc.tirserde import dict_to_type

ROOT = Path(__file__).resolve().parents[1]


def _script(tmp_path: Path, name: str, body: list[str]) -> Path:
    return write_case(tmp_path, name, "\n".join([*body, ""]))


def _run_both(tmp_path: Path, path: Path, argv: list[str]) -> tuple[str, str]:
    """Run the compiled binary and the CPython oracle with the same argv."""
    binary = tmp_path / path.stem
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary), *argv], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True, timeout=60)
    return native.stdout, oracle.stdout


def test_argparse_help_probe_survives_repeated_registration(tmp_path: Path) -> None:
    """A module-level parser with typed or required options compiles.

    Regression for #710: when a call site is pinned before it lowers for
    real, `add_argument` registers its option more than once, and the
    help-probe in `_parse_args` used to replay the duplicates into a fresh
    CPython ArgumentParser, which raised
    `ArgumentError: conflicting option string` and crashed the compile.
    """
    path = _script(
        tmp_path,
        "repeated.py",
        [
            "import argparse",
            "p = argparse.ArgumentParser()",
            "p.add_argument('--foo', type=int)",
            "p.add_argument('--bar', required=True)",
            "a = p.parse_args()",
            "print(a.foo)",
            "print(a.bar)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--foo", "3", "--bar", "x"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["3", "x"]


def test_argparse_default_prog_is_the_source_basename(tmp_path: Path) -> None:
    """The default argparse prog keeps the source `.py` name on the island floor.

    Regression for #711: the island seeded ``sys.argv[0]`` from the process
    argv, whose first element is the emitted binary path. argparse derives
    its default ``prog`` from ``os.path.basename(sys.argv[0])``, so a
    required-option error named the binary (``binary_size: error: ...``)
    instead of the source script (``binary_size.py: error: ...``), diverging
    from CPython's stderr for ``python binary_size.py``.
    """
    path = _script(
        tmp_path,
        "binary_size.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--foo', required=True, choices=['a', 'b'])",
            "a = parser.parse_args()",
            "print(a.foo)",
        ],
    )
    binary = tmp_path / "binary_size"
    got = run_cli(
        "compile", "--fallback=cpython", "--allow-host-libpython", str(path), "--emit", "bin", "-o", str(binary)
    )
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    assert native.returncode == oracle.returncode == 2
    assert native.stderr == oracle.stderr
    assert "binary_size.py: error:" in native.stderr


def test_argparse_flags_and_values(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "args.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('-c', '--contents', action='store_true')",
            "parser.add_argument('--name', default='world')",
            "parser.add_argument('--count', type=int, default='1')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.contents)",
            "print(args.name)",
            "print(args.count)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--contents", "--name=bob", "--count", "5"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["True", "bob", "5"]


def test_argparse_defaults_when_the_option_is_absent(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "defaults.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('-c', '--contents', action='store_true')",
            "parser.add_argument('--name', default='world')",
            "parser.add_argument('--count', type=int, default='1')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.contents)",
            "print(args.name)",
            "print(args.count)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, [])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["False", "world", "1"]


def test_argparse_value_in_the_next_token(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "spaced.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--name')",
            "parser.add_argument('--count', type=int)",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.name)",
            "print(args.count)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--name", "alice", "--count", "7"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["alice", "7"]


def test_argparse_short_option(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "short.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('-n', '--name')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.name)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["-n", "zoe"])
    assert native == oracle, (native, oracle)
    assert native.strip() == "zoe"


def test_argparse_dest_overrides_the_option_name(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "dest.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--dry-run', dest='dry')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.dry)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--dry-run", "yes"])
    assert native == oracle, (native, oracle)
    assert native.strip() == "yes"


def test_argparse_store_const_and_store_false(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "const.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--mode', action='store_const', const='fast')",
            "parser.add_argument('--quiet', action='store_false')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.mode)",
            "print(args.quiet)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--mode", "--quiet"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["fast", "False"]


def test_argparse_float_type(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "float.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--ratio', type=float)",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.ratio > 0.5)",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--ratio", "0.75"])
    assert native == oracle, (native, oracle)
    assert native.strip() == "True"


def test_argparse_positional_arguments(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "pos.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('src')",
            "    parser.add_argument('dst')",
            "    parser.add_argument('--count', type=int, default=1)",
            "    args = parser.parse_args()",
            "    print(args.src)",
            "    print(args.dst)",
            "    print(args.count)",
            "    return 0",
            "",
            "main()",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["in.txt", "out.txt", "--count=3"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["in.txt", "out.txt", "3"]


def test_argparse_unannotated_bindings(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "unannotated.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='echo_cli', description='Echo CLI tool.')",
            "    parser.add_argument('name', help='a name to greet')",
            "    parser.add_argument('--count', type=int, default=1, help='how many times')",
            "    parser.add_argument('--upper', action='store_true', help='uppercase the name')",
            "    args = parser.parse_args()",
            "    for _ in range(args.count):",
            "        if args.upper:",
            "            print(args.name.upper())",
            "        else:",
            "            print(args.name)",
            "    return 0",
            "",
            "main()",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["world", "--count", "2", "--upper"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["WORLD", "WORLD"]


def test_argparse_help_output(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "help_prog.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='my_cli', description='A demo CLI.')",
            "    parser.add_argument('file', help='file to process')",
            "    parser.add_argument('-n', '--num', type=int, default=10, help='number of items')",
            "    parser.add_argument('-v', '--verbose', action='store_true', help='verbose output')",
            "    args = parser.parse_args()",
            "    return 0",
            "",
            "main()",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--help"])
    assert native == oracle, (native, oracle)
    native_short, oracle_short = _run_both(tmp_path, path, ["-h"])
    assert native_short == oracle_short, (native_short, oracle_short)


def test_argparse_required_option(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "req.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--pattern', required=True)",
            "    args = parser.parse_args()",
            "    print(args.pattern)",
            "    return 0",
            "",
            "main()",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--pattern", "abc"])
    assert native == oracle, (native, oracle)
    assert native.strip() == "abc"

    binary = tmp_path / "req"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native_res = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    oracle_res = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    assert native_res.returncode == oracle_res.returncode
    assert native_res.returncode == 2


def test_argparse_missing_positional_fails(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "missing_pos.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('src')",
            "    args = parser.parse_args()",
            "    print(args.src)",
            "    return 0",
            "",
            "main()",
        ],
    )
    binary = tmp_path / "missing_pos"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60)
    assert native.returncode == oracle.returncode
    assert native.returncode == 2


def test_argparse_invalid_number_fails(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "invalid_num.py",
        [
            "import argparse",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser()",
            "    parser.add_argument('--count', type=int)",
            "    args = parser.parse_args()",
            "    print(args.count)",
            "    return 0",
            "",
            "main()",
        ],
    )
    binary = tmp_path / "invalid_num"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary), "--count", "notanint"], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run(
        [sys.executable, str(path), "--count", "notanint"], capture_output=True, text=True, timeout=60
    )
    assert native.returncode == oracle.returncode
    assert native.returncode == 2


def test_argparse_unknown_option_fails(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "unknown.py",
        [
            "import argparse",
            "",
            "parser: argparse.ArgumentParser = argparse.ArgumentParser()",
            "parser.add_argument('--name')",
            "args: argparse.Namespace = parser.parse_args()",
            "print(args.name)",
        ],
    )
    binary = tmp_path / "unknown"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary), "--bogus"], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(path), "--bogus"], capture_output=True, text=True, timeout=60)
    assert native.returncode == oracle.returncode
    assert native.returncode != 0


def test_argparse_keeps_the_closed_surface() -> None:
    from pyscriptc.lower import lower_source

    mod = lower_source("import argparse\nns = argparse.Namespace()\n")
    assert mod["fences"], "argparse.Namespace is outside the closed surface and must fence"
    mod = lower_source(
        "import argparse\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_argument('--name', nargs='A...')\n"
    )
    assert mod["fences"], "an unsupported nargs value must fence"
    assert "argparse nargs is an int count" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"

    mod = lower_source(
        "import argparse\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_argument('--unit', choices=('a', 'b'))\n"
    )
    assert mod["fences"]
    assert "argparse.add_argument keyword choices is not lowered" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"

    mod = lower_source(
        "import argparse\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_argument('--meta', metavar='M')\n"
    )
    assert mod["fences"]
    assert "argparse.add_argument keyword metavar is not lowered" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"

    mod = lower_source(
        "import argparse\n"
        "def custom_type(x: str) -> int: return 0\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_argument('--cust', type=custom_type)\n"
    )
    assert mod["fences"]
    assert "argparse type is str, int or float" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"

    mod = lower_source(
        "import argparse\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_mutually_exclusive_group()\n"
    )
    assert mod["fences"]
    assert "ArgParser.add_mutually_exclusive_group is not lowered" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"

    mod = lower_source("import argparse\nparser: argparse.ArgumentParser = argparse.ArgumentParser(usage='foo')\n")
    assert mod["fences"]
    assert "argparse.ArgumentParser keyword usage is not lowered" in mod["fences"][0]["message"]
    assert mod["fences"][0]["code"] == "PS2003"


def test_argparse_namespace_fields_follow_the_options() -> None:
    from pyscriptc.lower import lower_source
    from pyscriptc.tirserde import dict_to_type

    mod = lower_source(
        "import argparse\n"
        "\n"
        "def run() -> int:\n"
        "    parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--name')\n"
        "    parser.add_argument('--count', type=int)\n"
        "    parser.add_argument('-v', '--verbose', action='store_true')\n"
        "    args: argparse.Namespace = parser.parse_args()\n"
        "    return 0\n"
    )
    assert mod["fences"] == []
    namespace = next(cls for cls in mod["classes"] if cls["name"] == "Namespace")
    assert [(f["name"], dict_to_type(f["type"]).kind) for f in namespace["fields"]] == [
        ("name", "str"),
        ("count", "int"),
        ("verbose", "bool"),
    ]


def _peek_fields(source: str) -> list[dict[str, object]] | None:
    """The peek's field table for a module source, without lowering it.

    The peek reads only the AST and the lowerer's binding tables; the fake
    lowerer carries the two tables `_is_path_class` reads (empty here: the
    `type=Path` case is covered through the full lower in the pin test).
    """
    import ast

    import pyscriptc.lower  # noqa: F401  (the import order breaks a cycle)
    from pyscriptc.restlower._argparse import peek_namespace_fields

    class _FakeLowerer:
        bindings: dict[str, object] = {}
        constants: dict[str, object] = {}

        def _resolve_module(self, node: ast.expr) -> tuple[str, str, str] | None:
            return None

    tree = ast.parse(source)
    body = [stmt for stmt in tree.body if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))]
    flat: list[ast.stmt] = []
    for fn in body:
        flat.extend(fn.body)
    return peek_namespace_fields(_FakeLowerer(), flat)


def test_peek_namespace_fields_happy_path() -> None:
    fields = _peek_fields(
        "import argparse\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('work_dir', help='where')\n"
        "    parser.add_argument('--count', type=int, default=1)\n"
        "    parser.add_argument('--dry-run', action='store_true')\n"
        "    parser.add_argument('--ratio', type=float)\n"
        "    parser.add_argument('-v', '--verbose', action='store_false')\n"
        "    args = parser.parse_args()\n"
        "    return 0\n"
    )
    assert [(f["name"], dict_to_type(f["type"]).kind) for f in fields] == [
        ("work_dir", "str"),
        ("count", "int"),
        ("dry_run", "bool"),
        ("ratio", "float"),
        ("verbose", "bool"),
    ]


def test_peek_namespace_fields_skips_non_add_argument_calls() -> None:
    fields = _peek_fields(
        "import argparse\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser(prog='x')\n"
        "    parser.add_argument('name')\n"
        "    args = parser.parse_args()\n"
        "    return 0\n"
    )
    assert [(f["name"], dict_to_type(f["type"]).kind) for f in fields] == [("name", "str")]


@pytest.mark.parametrize(
    "kwarg",
    [
        "metavar='N'",
        "type=bool",
        "type=check",
        "nargs='A...'",
        "default=open('x')",
        "choices=['a']",
    ],
)
def test_peek_namespace_fields_fences_unknown_keywords(kwarg: str) -> None:
    assert (
        _peek_fields(
            "import argparse\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            f"    parser.add_argument('--count', {kwarg})\n"
            "    args = parser.parse_args()\n"
            "    return 0\n"
        )
        is None
    )


def test_peek_namespace_fields_fences_non_constant_name() -> None:
    assert (
        _peek_fields(
            "import argparse\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument(flag_name)\n"
            "    return 0\n"
        )
        is None
    )


def test_peek_namespace_fields_refuses_when_no_add_argument() -> None:
    assert _peek_fields("def main() -> int:\n    return 0\n") is None


def test_argparse_namespace_attribute_pins_a_parameter(tmp_path: Path) -> None:
    """An argument that is a namespace attribute pins from the field type.

    The pin pass runs before any body lowers, so `args.count` has no field
    type yet: the peek reads the same `add_argument` registrations the real
    lowering would make, purely from the AST, and `helper(args.count)`
    proves `count: int` without an annotation.
    """
    path = _script(
        tmp_path,
        "ns_pin.py",
        [
            "import argparse",
            "",
            "def helper(count):",
            "    return count * 2",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='ns_pin')",
            "    parser.add_argument('--count', type=int, default=1)",
            "    args = parser.parse_args()",
            "    print(helper(args.count))",
            "    return 0",
            "",
            "main()",
        ],
    )
    native, oracle = _run_both(tmp_path, path, ["--count", "21"])
    assert native == oracle, (native, oracle)
    assert native.splitlines() == ["42"]


def test_argparse_namespace_pin_refuses_an_unregistered_field(tmp_path: Path) -> None:
    """A field no registration declares keeps the parameter fence.

    The pin never guesses: `args.other` is not a field the parser
    registers, so the peek cannot type it and `other` stays fenced.
    """
    path = _script(
        tmp_path,
        "ns_no_field.py",
        [
            "import argparse",
            "",
            "def helper(other):",
            "    return 0",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='ns')",
            "    parser.add_argument('--count', type=int)",
            "    args = parser.parse_args()",
            "    helper(args.other)",
            "    return 0",
            "",
            "main()",
        ],
    )
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(tmp_path / "ns_no_field"))
    assert got.returncode != 0
    assert "parameter other needs an annotation" in got.stderr


def test_argparse_is_importable() -> None:
    from pyscriptc.stdlib_queue import QUEUE, module_status

    assert "argparse" in QUEUE
    assert module_status("argparse") == "lowered"
    assert (Path(ROOT) / "src" / "pyscriptc" / "runtime" / "ps_argparse.c").is_file()


_SUB_PROLOGUE = "import argparse\nparser: argparse.ArgumentParser = argparse.ArgumentParser(prog='tool')\n"


def _first_fence(source: str) -> dict[str, str]:
    from pyscriptc.lower import lower_source

    mod = lower_source(source)
    assert mod["fences"], source
    return mod["fences"][0]


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("parser.add_subparsers(prog='x')", "argparse.add_subparsers keyword prog is not lowered"),
        ("parser.add_subparsers(action='x')", "argparse.add_subparsers keyword action is not lowered"),
        ("parser.add_subparsers(title=1)", "argparse.add_subparsers title is a str constant"),
        ("parser.add_subparsers(parser_class=int)", "argparse.add_subparsers parser_class is argparse.ArgumentParser"),
        ("parser.add_subparsers(dest='c', required=1)", "argparse.add_subparsers required is a bool constant"),
        ("parser.add_subparsers(dest=1)", "argparse.add_subparsers dest is an identifier"),
        ("parser.add_subparsers('x')", "argparse.add_subparsers takes keyword arguments only"),
    ],
)
def test_argparse_add_subparsers_fences_the_unlowered_keywords(call: str, message: str) -> None:
    fence = _first_fence(_SUB_PROLOGUE + call + "\n")
    assert message in fence["message"]
    assert fence["code"] == "PS2003"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("sub.add_parser('a', usage='u')", "argparse.add_parser keyword usage is not lowered"),
        ("sub.add_parser('a', aliases='b')", "argparse.add_parser aliases is a list or tuple of str constants"),
        ("sub.add_parser('a', aliases=['--b'])", "argparse.add_parser aliases are str constants without dashes"),
        ("sub.add_parser('a'); sub.add_parser('b', aliases=['a'])", "argparse.add_parser name a is already registered"),
        ("sub.add_parser()", "argparse.add_parser takes exactly one name"),
        ("sub.add_parser('--a')", "argparse.add_parser name is a str constant without dashes or spaces"),
        ("sub.add_parser('a b')", "argparse.add_parser name is a str constant without dashes or spaces"),
        ("sub.add_parser('a', help=1)", "argparse.add_parser help is a str constant"),
        ("sub.add_parser('a'); sub.add_parser('a')", "argparse.add_parser name a is already registered"),
        ("sub.add_subparsers()", "SubParsers.add_subparsers is not lowered"),
    ],
)
def test_argparse_add_parser_fences_the_unlowered_shapes(call: str, message: str) -> None:
    fence = _first_fence(_SUB_PROLOGUE + "sub = parser.add_subparsers(dest='cmd')\n" + call + "\n")
    assert message in fence["message"]
    assert fence["code"] == "PS2003"


def test_argparse_subparsers_fence_a_second_holder_on_one_parser() -> None:
    source = _SUB_PROLOGUE + "parser.add_subparsers(dest='a')\nparser.add_subparsers(dest='b')\n"
    assert "one add_subparsers call per parser" in _first_fence(source)["message"]
    # A nested holder on a subcommand parser is not a second holder.
    from pyscriptc.lower import lower_source

    nested = (
        _SUB_PROLOGUE
        + "sub = parser.add_subparsers(dest='cmd')\n"
        + "child = sub.add_parser('a')\n"
        + "inner = child.add_subparsers(dest='inner')\n"
        + "inner.add_parser('x')\n"
    )
    assert not lower_source(nested)["fences"]


def test_argparse_add_parser_needs_a_subparsers_holder() -> None:
    fence = _first_fence(_SUB_PROLOGUE + "parser.add_parser('a')\n")
    assert "ArgParser.add_parser is not lowered" in fence["message"]
    assert fence["code"] == "PS2003"


def test_peek_namespace_fields_registers_the_subparsers_dest_in_program_order() -> None:
    import ast

    from pyscriptc.lower import Lowerer
    from pyscriptc.restlower._argparse import peek_namespace_fields

    body = ast.parse(
        "parser.add_argument('--top')\n"
        "sub = parser.add_subparsers(dest='cmd', required=True)\n"
        "run = sub.add_parser('run')\n"
        "run.add_argument('--n', type=int)\n"
    ).body
    lo = Lowerer("t.py", "")
    fields = peek_namespace_fields(lo, body)
    assert fields is not None
    assert [f["name"] for f in fields] == ["top", "cmd", "n"]
    assert peek_namespace_fields(lo, ast.parse("parser.add_subparsers(prog='x')").body) is None
    # Without a dest the holder registers no field.
    assert peek_namespace_fields(lo, ast.parse("parser.add_subparsers()\nparser.add_argument('--a')").body) is not None


_SUB_SOURCE = [
    "import argparse",
    "import sys",
    "",
    "def main() -> int:",
    "    parser = argparse.ArgumentParser(prog='tool')",
    "    parser.add_argument('--verbose', action='store_true')",
    "    sub = parser.add_subparsers(dest='cmd', required=True)",
    "    enq = sub.add_parser('enqueue', help='queue a job')",
    "    enq.add_argument('--name', '-n', required=True)",
    "    enq.add_argument('--retries', type=int, default=3)",
    "    st = sub.add_parser('status', help='show a job')",
    "    st.add_argument('job')",
    "    st.add_argument('--ratio', type=float, default=0.5)",
    "    args = parser.parse_args(sys.argv[1:])",
    "    if args.cmd == 'enqueue':",
    "        print(args.cmd, args.verbose, args.name, args.retries)",
    "    else:",
    "        print(args.cmd, args.verbose, args.job, args.ratio)",
    "    return 0",
    "",
    "main()",
]

_SUB_ARGVS = [
    [],
    ["x"],
    ["enqueue"],
    ["enqueue", "--name"],
    ["enqueue", "--name", "a", "--retries", "zz"],
    ["enqueue", "--name", "a", "--bogus"],
    ["enqueue", "--name", "a", "extra"],
    ["--bogus", "status", "j"],
    ["status"],
    ["status", "a", "b"],
    ["status", "j", "--verbose"],
    ["status", "j", "--ratio", "x"],
    ["-h"],
    ["--help", "status"],
    ["enqueue", "-h"],
    ["status", "--help"],
    ["enqueue", "-n", "q", "--retries=5"],
    ["--verbose", "status", "j", "--ratio", "2.5"],
]


def test_argparse_subparsers_match_cpython_bytes(tmp_path: Path) -> None:
    """Stdout, stderr and the exit code agree with CPython on every path.

    The success paths read the dest and the chosen subcommand's arguments;
    the error paths cover a missing and an unknown subcommand, a missing
    required option, a bad value, an unrecognized token and the help of
    each level. The prog is explicit so the usage line is source-independent.
    """
    path = _script(tmp_path, "subs.py", _SUB_SOURCE)
    binary = tmp_path / "subs"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    for argv in _SUB_ARGVS:
        native = subprocess.run([str(binary), *argv], capture_output=True, text=True, timeout=60)
        oracle = subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True, timeout=60)
        assert (native.stdout, native.stderr, native.returncode) == (oracle.stdout, oracle.stderr, oracle.returncode), (
            argv
        )


def test_argparse_subparsers_without_a_dest_label_the_choices(tmp_path: Path) -> None:
    path = _script(
        tmp_path,
        "nodest.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='tool')",
            "    sub = parser.add_subparsers(required=True)",
            "    a = sub.add_parser('a')",
            "    a.add_argument('--x', type=int, default=1)",
            "    sub.add_parser('b')",
            "    args = parser.parse_args(sys.argv[1:])",
            "    print('ok')",
            "    return 0",
            "",
            "main()",
        ],
    )
    binary = tmp_path / "nodest"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    for argv in ([], ["z"], ["a", "--x", "3"], ["b"]):
        native = subprocess.run([str(binary), *argv], capture_output=True, text=True, timeout=60)
        oracle = subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True, timeout=60)
        assert (native.stdout, native.stderr, native.returncode) == (oracle.stdout, oracle.stderr, oracle.returncode), (
            argv
        )


def test_argparse_optional_subcommand_reads_empty_dest(tmp_path: Path) -> None:
    """An absent optional subcommand reads "" where CPython reads None (the recorded divergence)."""
    path = _script(
        tmp_path,
        "optional.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    parser = argparse.ArgumentParser(prog='tool')",
            "    parser.add_argument('--level', type=int, default=1)",
            "    sub = parser.add_subparsers(dest='action')",
            "    sub.add_parser('go')",
            "    args = parser.parse_args(sys.argv[1:])",
            "    print(args.level, repr(args.action))",
            "    return 0",
            "",
            "main()",
        ],
    )
    binary = tmp_path / "optional"
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary), "--level", "4"], capture_output=True, text=True, timeout=60)
    oracle = subprocess.run([sys.executable, str(path), "--level", "4"], capture_output=True, text=True, timeout=60)
    assert native.stdout == "4 ''\n"
    assert oracle.stdout == "4 None\n"
    chosen = subprocess.run([str(binary), "go"], capture_output=True, text=True, timeout=60)
    assert chosen.stdout == "1 'go'\n"


def _compile_and_compare(tmp_path: Path, name: str, body: list[str], argvs: list[list[str]]) -> None:
    """Compile once, run every argv natively and under CPython, compare stdout, stderr and the exit code."""
    path = _script(tmp_path, name, body)
    binary = tmp_path / path.stem
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    for argv in argvs:
        native = subprocess.run([str(binary), *argv], capture_output=True, text=True, timeout=60)
        oracle = subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True, timeout=60)
        assert (native.stdout, native.stderr, native.returncode) == (oracle.stdout, oracle.stderr, oracle.returncode), (
            argv
        )


def test_argparse_nargs_positionals_and_options_match_cpython(tmp_path: Path) -> None:
    _compile_and_compare(
        tmp_path,
        "nargs_mix.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    p = argparse.ArgumentParser(prog='t')",
            "    p.add_argument('--v', action='store_true')",
            "    p.add_argument('-n', '--num', type=int, nargs='?', const=9, default=1)",
            "    p.add_argument('--l', nargs='+')",
            "    p.add_argument('--pair', nargs=2, type=float)",
            "    p.add_argument('a')",
            "    p.add_argument('b', nargs='*')",
            "    args = p.parse_args(sys.argv[1:])",
            "    print(args.v, args.num, args.a, args.b)",
            "    return 0",
            "",
            "main()",
        ],
        [
            [],
            ["x"],
            ["x", "y", "z"],
            ["--v", "x", "y"],
            ["x", "--v", "y"],
            ["-n"],
            ["-n", "x"],
            ["--num=3", "x"],
            ["x", "--l", "p", "q"],
            ["--l"],
            ["--pair", "1"],
            ["--pair", "1", "2.5", "x"],
            ["--pair", "1", "w", "x"],
            ["--", "x", "--v"],
            ["x", "--", "-1", "--v"],
            ["x", "-5"],
            ["x", "--bad"],
            ["-h"],
        ],
    )


def test_argparse_nargs_remainder_after_a_command_word(tmp_path: Path) -> None:
    """The motivating shape of #1373: a command word, then REMAINDER takes every token after it."""
    _compile_and_compare(
        tmp_path,
        "nargs_rem.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    p = argparse.ArgumentParser(prog='t')",
            "    p.add_argument('--x', nargs=2)",
            "    p.add_argument('cmd')",
            "    p.add_argument('rest', nargs=argparse.REMAINDER)",
            "    args = p.parse_args(sys.argv[1:])",
            "    print(args.cmd, args.rest)",
            "    return 0",
            "",
            "main()",
        ],
        [
            [],
            ["go"],
            ["go", "-a", "--x", "1"],
            ["--x", "1", "2", "go", "--x", "--", "foo"],
            ["go", "--", "--v"],
            ["--x", "1"],
        ],
    )


def test_argparse_subcommands_nest_alias_and_follow_registration_order(tmp_path: Path) -> None:
    _compile_and_compare(
        tmp_path,
        "nested.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    p = argparse.ArgumentParser(prog='t', epilog='bye')",
            "    p.add_argument('pre')",
            "    sub = p.add_subparsers(dest='cmd', required=True, title='cmds', "
            "description='pick one', metavar='CMD')",
            "    p.add_argument('post', nargs='?', default='P')",
            "    run = sub.add_parser('run', aliases=['r'], help='run it', description='runs', epilog='see')",
            "    run.add_argument('w', nargs='*')",
            "    rem = sub.add_parser('remote')",
            "    inner = rem.add_subparsers(dest='action', required=True)",
            "    add = inner.add_parser('add', aliases=['a'])",
            "    add.add_argument('urls', nargs='+')",
            "    args = p.parse_args(sys.argv[1:])",
            "    print(args.pre, args.cmd, args.post)",
            "    if args.cmd == 'remote':",
            "        print(args.action, args.urls)",
            "    else:",
            "        print(args.w)",
            "    return 0",
            "",
            "main()",
        ],
        [
            [],
            ["pre"],
            ["pre", "run"],
            ["pre", "r", "a", "b"],
            ["pre", "run", "a", "--", "b"],
            ["pre", "remote"],
            ["pre", "remote", "add"],
            ["pre", "remote", "a", "u1", "u2"],
            ["pre", "nope"],
            ["pre", "remote", "zap"],
            ["-h"],
            ["pre", "run", "-h"],
            ["pre", "remote", "-h"],
            ["pre", "remote", "add", "-h"],
        ],
    )


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("p.add_argument('--a', nargs='A...')", "argparse nargs is an int count"),
        ("p.add_argument('--a', nargs=0)", "argparse nargs is an int count"),
        ("p.add_argument('--a', nargs=True)", "argparse nargs is an int count"),
        ("p.add_argument('--a', nargs='*', default='x')", "argparse nargs list arguments take no default"),
        ("p.add_argument('a', nargs='?')", "argparse nargs='?' needs a default"),
        ("p.add_argument('--a', nargs='?', default='x')", "argparse nargs='?' option needs a const"),
        ("p.add_argument('--a', nargs=2, action='store_true')", "argparse nargs takes the store action"),
        ("p.add_argument('--a', nargs=2, type=Path)", "argparse type=Path does not take nargs"),
        ("p.add_argument('--a', const='x')", "argparse const takes the store_const action or nargs='?'"),
    ],
)
def test_argparse_nargs_fences_the_unlowered_shapes(call: str, message: str) -> None:
    prologue = (
        "import argparse\nfrom pathlib import Path\np: argparse.ArgumentParser = argparse.ArgumentParser(prog='t')\n"
    )
    fence = _first_fence(prologue + call + "\n")
    assert message in fence["message"]
    assert fence["code"] == "PS2003"


def test_peek_namespace_fields_types_a_list_valued_nargs() -> None:
    fields = _peek_fields(
        "import argparse\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('files', nargs='*')\n"
        "    parser.add_argument('--n', nargs='+', type=int)\n"
        "    parser.add_argument('--one', nargs='?', default='x', const='y')\n"
        "    args = parser.parse_args()\n"
        "    return 0\n"
    )
    assert fields is not None
    assert [(f["name"], f["type"]["kind"]) for f in fields] == [("files", "list"), ("n", "list"), ("one", "str")]
    assert fields[1]["type"]["elem"]["kind"] == "int"


def test_argparse_shared_dest_across_levels_matches_cpython(tmp_path: Path) -> None:
    """A dest a parent and a subcommand (or two siblings) both register is one attribute.

    The chosen level's value lands: a token the chosen level consumes
    overwrites the parent's value, the chosen level's default overwrites the
    parent's default, and an unchosen sibling's default does not leak. The
    shared dest is read only on the paths that register it, because an
    unchosen subcommand's attribute is the recorded divergence.
    """
    _compile_and_compare(
        tmp_path,
        "shared.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    p = argparse.ArgumentParser(prog='t')",
            "    p.add_argument('--x', type=int, default=1)",
            "    s = p.add_subparsers(dest='cmd', required=True)",
            "    go = s.add_parser('go', help='go mode')",
            "    go.add_argument('--x', type=int, default=2)",
            "    go.add_argument('word')",
            "    stop = s.add_parser('stop', help='stop mode')",
            "    stop.add_argument('--x', type=int, default=3)",
            "    args = p.parse_args(sys.argv[1:])",
            "    if args.cmd == 'go':",
            "        print('go', args.x, args.word)",
            "    else:",
            "        print('stop', args.x)",
            "    return 0",
            "",
            "main()",
        ],
        [
            ["go", "hi"],
            ["go", "--x", "9", "hi"],
            ["--x", "5", "go", "hi"],
            ["--x", "5", "stop"],
            ["stop"],
            ["go", "hi"],
        ],
    )


def test_argparse_shared_dest_between_siblings_matches_cpython(tmp_path: Path) -> None:
    """Two sibling subcommands registering one dest name share one attribute."""
    _compile_and_compare(
        tmp_path,
        "siblings.py",
        [
            "import argparse",
            "import sys",
            "",
            "def main() -> int:",
            "    p = argparse.ArgumentParser(prog='t')",
            "    s = p.add_subparsers(dest='cmd', required=True)",
            "    a = s.add_parser('a')",
            "    a.add_argument('x', type=int)",
            "    b = s.add_parser('b')",
            "    b.add_argument('x', type=int)",
            "    args = p.parse_args(sys.argv[1:])",
            "    print(args.cmd, args.x)",
            "    return 0",
            "",
            "main()",
        ],
        [["a", "5"], ["b", "7"], ["a", "0"], ["b", "42"]],
    )


def test_argparse_shared_dest_kind_conflict_fences() -> None:
    """A dest registered twice with different types stops the compile."""
    fence = _first_fence(
        _SUB_PROLOGUE
        + "sub = parser.add_subparsers(dest='cmd')\n"
        + "a = sub.add_parser('a')\na.add_argument('x', type=int)\n"
        + "b = sub.add_parser('b')\nb.add_argument('x', type=str)\n"
    )
    assert "argparse dest x is registered twice with different types" in fence["message"]
    assert fence["code"] == "PS2003"


def test_peek_namespace_fields_dedupes_a_shared_dest() -> None:
    fields = _peek_fields(
        "import argparse\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        "    sub = parser.add_subparsers(dest='cmd')\n"
        "    a = sub.add_parser('a')\n"
        "    a.add_argument('x', type=int)\n"
        "    b = sub.add_parser('b')\n"
        "    b.add_argument('x', type=int)\n"
        "    args = parser.parse_args()\n"
        "    return 0\n"
    )
    assert fields is not None
    assert [(f["name"], f["type"]["kind"]) for f in fields] == [("cmd", "str"), ("x", "int")]
