"""argparse: the script subset of the command-line parser.

One `ArgumentParser`, a few `add_argument` calls and `parse_args`. The
parser keeps its options in a list of encoded specs, `parse_args` walks
the process argv, and the namespace carries one field per option.

The subset reads the option spellings, `dest`, `action`, `const`,
`default`, `type` and `help`. `type` is `str`, `int` or `float`. The
`help` text is accepted and dropped: the subset does not print help.

Each probe compiles to a binary, runs it with the same argv the CPython
oracle gets, and diffs the stdout.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

ROOT = Path(__file__).resolve().parents[1]


def _script(tmp_path: Path, name: str, body: list[str]) -> Path:
    return write_case(tmp_path, name, "\n".join([*body, ""]))


def _run_both(tmp_path: Path, path: Path, argv: list[str]) -> tuple[str, str]:
    """Run the compiled binary and the CPython oracle with the same argv."""
    binary = tmp_path / path.stem
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary), *argv], capture_output=True, text=True)
    oracle = subprocess.run([sys.executable, str(path), *argv], capture_output=True, text=True)
    return native.stdout, oracle.stdout


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
    native = subprocess.run([str(binary), "--bogus"], capture_output=True, text=True)
    oracle = subprocess.run([sys.executable, str(path), "--bogus"], capture_output=True, text=True)
    assert native.returncode == oracle.returncode
    assert native.returncode != 0


def test_argparse_keeps_the_closed_surface() -> None:
    from pyscriptc.lower import lower_source

    mod = lower_source("import argparse\nparser = argparse.ArgumentParser()\n")
    assert mod["fences"], "an unannotated parser binding must fence"
    mod = lower_source(
        "import argparse\n"
        "parser: argparse.ArgumentParser = argparse.ArgumentParser()\n"
        "parser.add_argument('--name', nargs=2)\n"
    )
    assert mod["fences"], "an unknown add_argument keyword must fence"
    assert "argparse.add_argument keyword nargs is not lowered" in mod["fences"][0]["message"]


def test_argparse_namespace_fields_follow_the_options() -> None:
    from pyscriptc.lower import lower_source

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
    assert [(f["name"], f["type"]["kind"]) for f in namespace["fields"]] == [
        ("name", "str"),
        ("count", "int"),
        ("verbose", "bool"),
    ]


def test_argparse_is_importable() -> None:
    from pyscriptc.stdlib_queue import QUEUE, module_status

    assert "argparse" in QUEUE
    assert module_status("argparse") == "lowered"
    assert (Path(ROOT) / "src" / "pyscriptc" / "runtime" / "ps_argparse.c").is_file()
