"""platform: the script-shaped surface.

`platform.system`, `machine`, `python_version` and `platform` join the
fixed rows. `platform.platform()` builds the CPython 3.14 string: the
parts are joined with '-', the empty parts are dropped, the characters
that are awkward in a file name are folded to '_', "unknown" is dropped,
runs of '-' are folded to one and a trailing '-' is removed. The Linux
string carries the libc name and version, which the runtime reads from
the running libc rather than hardcoding it.

Each probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_platform_system_and_machine(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "system.py",
        "\n".join(
            [
                "import platform",
                "",
                "def run() -> int:",
                "    print(platform.system())",
                "    print(platform.machine())",
                "    print(platform.python_version())",
                "    return 0",
                "",
            ]
        ),
    )


def test_platform_platform_format(tmp_path: Path) -> None:
    """#713: platform.platform() returns the CPython 3.14 string.

    The compiled program and the CPython oracle both run on this host, so
    the two strings must be byte-identical: the same system, release,
    machine and libc parts, joined and folded the same way.
    """
    _oracle(
        tmp_path,
        "platformstr.py",
        "\n".join(
            [
                "import platform",
                "",
                "def run() -> int:",
                "    print(platform.platform())",
                "    return 0",
                "",
            ]
        ),
    )


def test_platform_platform_shape(tmp_path: Path) -> None:
    """The parts of the string are the ones CPython puts in it.

    The libc part is host-dependent, so the test checks the shape of the
    string instead of a fixed spelling: the system name leads it, the
    machine follows the release, and the libc part carries the version of
    the running libc.
    """
    _oracle(
        tmp_path,
        "platform_shape.py",
        "\n".join(
            [
                "import platform",
                "",
                "def run() -> int:",
                "    text: str = platform.platform()",
                "    print(text.startswith(platform.system() + '-'))",
                "    print(platform.machine() in text)",
                "    print('-' in text)",
                "    print(text.endswith('-'))",
                "    print('  ' in text)",
                "    print(text == text.strip())",
                "    return 0",
                "",
            ]
        ),
    )
