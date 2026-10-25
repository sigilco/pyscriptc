"""Issue #603: an uncaught `SystemExit` sets the process exit status.

CPython's last-resort handler treats `SystemExit` specially: an `int` value is
the exit status, a `str` value is written to stderr and the process exits 1,
and no value exits 0. The compiled program used to print `SystemExit: <value>`
to stderr and exit 1 for every one of those, in the linked binary and in
`pyscriptc run`. Every case here is compared byte for byte against CPython
running the same source.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc import cli

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

CASES: list[tuple[str, str]] = [
    (
        "value_from_main",
        "def main() -> int:\n"
        '    print("hello")\n'
        "    return 0\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
    ),
    (
        "nonzero_from_main",
        "def main() -> int:\n"
        '    print("hello")\n'
        "    return 3\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
    ),
    (
        "literal_zero",
        "raise SystemExit(0)\n",
    ),
    (
        "negative_value",
        "raise SystemExit(-1)\n",
    ),
    (
        "no_value",
        "raise SystemExit()\n",
    ),
    (
        "string_value",
        'raise SystemExit("bye")\n',
    ),
    (
        "string_value_with_newline",
        'raise SystemExit("bye\\n")\n',
    ),
    (
        "string_value_with_cause",
        'raise SystemExit("bye") from ValueError("x")\n',
    ),
    (
        "bool_value",
        "raise SystemExit(True)\n",
    ),
    (
        "float_value",
        "raise SystemExit(1.5)\n",
    ),
    (
        "caught_value_stays_catchable",
        "def main() -> int:\n"
        "    try:\n"
        "        raise SystemExit(3)\n"
        "    except SystemExit as e:\n"
        "        print(str(e))\n"
        "        return 0\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
    ),
]


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(home))
    monkeypatch.delenv("PYSC_NO_RUN_CACHE", raising=False)
    return home


def _build(tmp_path: Path, source: str) -> tuple[Path, Path]:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    binary = tmp_path / "prog_bin"
    assert cli.main(["compile", str(src), "--emit", "bin", "--skip-check", "-o", str(binary)]) == 0
    return src, binary


@needs_clang
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_uncaught_system_exit_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    src, binary = _build(tmp_path, source)
    want = subprocess.run([sys.executable, str(src)], capture_output=True)
    got = subprocess.run([str(binary)], capture_output=True)
    assert got.returncode == want.returncode, (got.returncode, want.returncode, got.stderr)
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr


def test_status_with_a_cause_is_fenced(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """A raise-from record has no room for the status, so the shape fences."""
    src = tmp_path / "cause.py"
    src.write_text('raise SystemExit(3) from ValueError("x")\n', encoding="utf-8")
    assert cli.main(["compile", str(src), "--skip-check"]) != 0
    assert "PS2003: raise SystemExit with a status and a cause is not lowered" in capfd.readouterr().err


@needs_clang
def test_run_propagates_the_system_exit_status(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "prog.py"
    src.write_text(
        "def main() -> int:\n"
        '    print("hello")\n'
        "    return 3\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    assert cli.main(["run", str(src), "--skip-check"]) == 3
    captured = capfd.readouterr()
    assert captured.out == "hello\n"
    # `run` names the cached binary on stderr; the program itself reports nothing.
    assert "SystemExit" not in captured.err


# Island seam cases (issue #642): the `SystemExit` escapes a fenced (island)
# function, so delivery goes through the island runtime's failure path. Each
# is compiled with `--fallback=cpython --emit bin` and compared against
# CPython on exit code and stderr.
ISLAND_CASES: list[tuple[str, str]] = [
    (
        "str_payload_from_island_fn",
        "import os\n"
        "import sys\n"
        "\n"
        "\n"
        "def fail(n: int) -> None:\n"
        '    for root, dirs, files in os.walk("/nonexistent"):\n'
        "        pass\n"
        "    if n > 0:\n"
        '        sys.exit("boom")\n'
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail(1)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
    (
        "raise_str_payload_from_island_fn",
        "import sys\n"
        "\n"
        "\n"
        "def fail() -> None:\n"
        '    raise SystemExit("boom")\n'
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
    (
        "int_payload_from_island_fn",
        "import sys\n"
        "\n"
        "\n"
        "def fail() -> None:\n"
        "    sys.exit(3)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
    (
        "none_payload_from_island_fn",
        "import sys\n"
        "\n"
        "\n"
        "def fail() -> None:\n"
        "    sys.exit(None)\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
    (
        "no_payload_from_island_fn",
        "import sys\n"
        "\n"
        "\n"
        "def fail() -> None:\n"
        "    sys.exit()\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
    (
        "nonstring_payload_from_island_fn",
        "import sys\n"
        "\n"
        "\n"
        "def fail() -> None:\n"
        "    sys.exit((1, 2))\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    fail()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    ),
]


@needs_clang
@pytest.mark.parametrize("name,source", ISLAND_CASES, ids=[name for name, _ in ISLAND_CASES])
def test_island_system_exit_matches_cpython(tmp_path: Path, name: str, source: str) -> None:
    src = tmp_path / "prog.py"
    src.write_text(source, encoding="utf-8")
    binary = tmp_path / "prog_bin"
    assert (
        cli.main(["compile", str(src), "--fallback=cpython", "--emit", "bin", "--skip-check", "-o", str(binary)]) == 0
    )
    want = subprocess.run([sys.executable, str(src)], capture_output=True)
    got = subprocess.run([str(binary)], capture_output=True)
    assert got.returncode == want.returncode, (got.returncode, want.returncode, got.stderr)
    assert got.stdout == want.stdout
    assert got.stderr == want.stderr
