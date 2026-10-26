"""Issue #645: `-> NoReturn` and `sys.exit(str)` lower instead of fencing.

Two idioms every CLI uses fenced otherwise lowerable definitions:

- `def status(args: argparse.Namespace) -> NoReturn:` fenced with PS1103
  ("NoReturn is not a lowered type in the IR").
- `sys.exit("message")` fenced with PS1003 ("sys.exit argument is an int").

The bottom type lowers to a `none` return: the function never hands a value
back to its caller, so the caller reads no result. `sys.exit(str)` rides the
`SystemExit` raise path, so CPython's rules hold in both places: the
uncaught handler writes the message to stderr and exits 1, and an
`except SystemExit` still catches it.

Every case is compared byte for byte against CPython running the same source.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import cli

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

# (name, source): the `-> NoReturn` and `sys.exit(str)` spellings a CLI uses.
CASES: list[tuple[str, str]] = [
    (
        "noreturn_raises_system_exit",
        "import sys\n"
        "from typing import NoReturn\n"
        "\n"
        "\n"
        "def status(code: int) -> NoReturn:\n"
        '    print("status")\n'
        "    sys.exit(code)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        '    print("before")\n'
        "    status(3)\n"
        '    print("after")\n'
        "    return 0\n"
        "\n"
        "\n"
        "main()\n",
    ),
    (
        "never_spelling",
        "from typing import Never\n"
        "\n"
        "\n"
        "def die() -> Never:\n"
        '    print("dying")\n'
        "    raise SystemExit(1)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    die()\n"
        "    return 0\n"
        "\n"
        "\n"
        "main()\n",
    ),
    (
        "qualified_noreturn",
        "import typing\n"
        "\n"
        "\n"
        "def die() -> typing.NoReturn:\n"
        "    raise SystemExit(2)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    die()\n"
        "    return 0\n"
        "\n"
        "\n"
        "main()\n",
    ),
    (
        "sys_exit_with_a_string",
        "import sys\n"
        "\n"
        "\n"
        "def die(message: str) -> None:\n"
        "    sys.exit(message)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        '    print("before")\n'
        '    die("boom")\n'
        '    print("after")\n'
        "    return 0\n"
        "\n"
        "\n"
        "main()\n",
    ),
    (
        "sys_exit_string_is_catchable",
        "import sys\n"
        "\n"
        "\n"
        "def die(message: str) -> None:\n"
        "    sys.exit(message)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    try:\n"
        '        die("caught")\n'
        "    except SystemExit as exc:\n"
        '        print(f"got {exc}")\n'
        "    return 0\n"
        "\n"
        "\n"
        "main()\n",
    ),
]


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(home))
    monkeypatch.delenv("PYSC_NO_RUN_CACHE", raising=False)
    return home


def _build(tmp_path: Path, source: str, name: str = "prog") -> tuple[Path, Path]:
    src = tmp_path / f"{name}.py"
    src.write_text(source, encoding="utf-8")
    binary = tmp_path / f"{name}_bin"
    assert cli.main(["compile", str(src), "--emit", "bin", "--skip-check", "-o", str(binary)]) == 0
    return src, binary


@needs_clang
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_cli_idiom_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    src, binary = _build(tmp_path, source)
    want = subprocess.run([sys.executable, str(src)], capture_output=True)
    got = subprocess.run([str(binary)], capture_output=True)
    assert got.returncode == want.returncode, (got.returncode, want.returncode, got.stderr)
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr


@needs_clang
def test_sys_exit_string_reports_the_message_and_exits_one(tmp_path: Path) -> None:
    """The message goes to stderr, byte for byte as CPython writes it."""
    src, binary = _build(
        tmp_path,
        'import sys\n\n\ndef main() -> None:\n    sys.exit("boom")\n\n\nmain()\n',
    )
    want = subprocess.run([sys.executable, str(src)], capture_output=True)
    got = subprocess.run([str(binary)], capture_output=True)
    assert got.returncode == 1
    assert got.stderr == b"boom\n"
    assert got.stderr == want.stderr


@needs_clang
def test_noreturn_definition_is_not_fenced(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """`-> NoReturn` no longer refuses the file with PS1103."""
    src = tmp_path / "noreturn.py"
    src.write_text(
        "import argparse\n"
        "from typing import NoReturn\n"
        "\n"
        "\n"
        "def status(args: argparse.Namespace) -> NoReturn:\n"
        '    print("status")\n'
        "    raise SystemExit(1)\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--x")\n'
        "    args = parser.parse_args()\n"
        "    status(args)\n"
        "    return 0\n",
        encoding="utf-8",
    )
    assert cli.main(["compile", str(src), "--skip-check"]) == 0
    assert "PS1103" not in capfd.readouterr().err


def test_sys_exit_string_no_longer_fences_with_ps1003(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """`sys.exit(str)` lowers; the PS1003 fence is gone."""
    src = tmp_path / "exitstr.py"
    src.write_text(
        "import sys\n\n\ndef die(message: str) -> None:\n    sys.exit(message)\n",
        encoding="utf-8",
    )
    assert cli.main(["compile", str(src), "--skip-check"]) == 0
    err = capfd.readouterr().err
    assert "PS1003" not in err


def test_sys_exit_with_an_unsupported_argument_still_fences(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """Only an int or a str is lowered; a tuple still refuses with PS1003."""
    src = tmp_path / "exitpair.py"
    src.write_text(
        "import sys\n\n\ndef die() -> None:\n    sys.exit((1, 2))\n",
        encoding="utf-8",
    )
    assert cli.main(["compile", str(src), "--skip-check"]) != 0
    assert "PS1003" in capfd.readouterr().err


@needs_clang
def test_island_fallback_matches_cpython_for_sys_exit_string(tmp_path: Path) -> None:
    """Under `--fallback=cpython` the definition rides the island; the
    observable behaviour is the same as the native one and as CPython."""
    src = tmp_path / "island.py"
    src.write_text(
        "import os\n"
        "import sys\n"
        "\n"
        "\n"
        "def die(message: str) -> None:\n"
        '    for root, dirs, files in os.walk("/nonexistent"):\n'
        "        print(root)\n"
        "    sys.exit(message)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        '    die("boom")\n'
        "\n"
        "\n"
        "main()\n",
        encoding="utf-8",
    )
    binary = tmp_path / "island_bin"
    assert (
        cli.main(["compile", str(src), "--fallback=cpython", "--emit", "bin", "--skip-check", "-o", str(binary)]) == 0
    )
    want = subprocess.run([sys.executable, str(src)], capture_output=True)
    got = subprocess.run([str(binary)], capture_output=True)
    assert got.returncode == want.returncode, (got.returncode, want.returncode, got.stderr)
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr
