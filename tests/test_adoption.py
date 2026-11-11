"""Libraries an early scripting user already has stay on CPython until the subset grows."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import run_cli, write_case


def test_scripting_stacks_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("click.py", "import click\n\ndef main() -> int:\n    return 0\n", "PS2002", "click"),
        (
            "kw.py",
            "import math\n\ndef main() -> float:\n    return math.sqrt(x=4.0)\n",
            "PS2003",
            "keyword arguments",
        ),
        ("star.py", "def main(**kwargs) -> int:\n    return 0\n", "PS1202", "keyword forwarding (**kwargs)"),
        (
            "async.py",
            "import asyncio\n\ndef main() -> int:\n    asyncio.run(None)\n    return 1\n",
            "PS2002",
            "takes a coroutine",
        ),
        (
            "fstr.py",
            "def main() -> str:\n    name: str = 'a'\n    return f'{name:{name!r}}'\n",
            "PS2002",
            "f-string",
        ),
    ]
    for name, source, code, text in cases:
        path = write_case(tmp_path, name, source)
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
        assert code in got.stderr
        assert text in got.stderr


def test_a_user_decorator_takes_the_advisory_erase(tmp_path: Path) -> None:
    # A user decorator outside the closed set compiles as erased with the
    # advisory PS3099 note (divergence 54, issue #732): the def lowers
    # undecorated and the wrap never runs.
    path = write_case(
        tmp_path,
        "deco.py",
        "def deco(fn: int) -> int:\n    return fn\n\n@deco\ndef main() -> int:\n    return 0\n",
    )
    got = run_cli(
        "compile",
        str(path),
        "--skip-check",
        "--emit",
        "llvm",
        "-o",
        str(tmp_path / "deco.ll"),
    )
    assert got.returncode == 0, got.stderr
    assert "PS3099" in got.stderr, got.stderr
    assert "Traceback" not in got.stderr, got.stderr
