"""os.system diffed against the CPython oracle."""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _oracle(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run"), got.stdout


def _fence(source: str, code: str, message: str) -> None:
    mod = lower_source(source)
    fences = mod["fences"]
    assert fences, source
    assert fences[0]["code"] == code, fences
    assert message in fences[0]["message"], fences


def test_system_exit_status_matches_cpython(tmp_path: Path) -> None:
    """The raw wait status CPython reports, not the decoded exit code."""
    _oracle(
        tmp_path,
        "system_status.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                '    code: int = os.system("exit 3")',
                "    print(code)",
                "    print(code == 3 * 256)",
                '    code = os.system("true")',
                "    print(code)",
                '    code = os.system("exit 255")',
                "    print(code)",
                "    return 0",
                "",
            ]
        ),
    )


def test_system_shell_semantics_match_cpython(tmp_path: Path) -> None:
    """The command rides the shell: redirection and a pipe are its business."""
    _oracle(
        tmp_path,
        "system_shell.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                '    path = "os_system_test_out.txt"',
                '    os.system("echo hello > " + path)',
                '    with open(path, "r") as handle:',
                "        print(handle.read())",
                '    print(os.system("echo piped | tr a-z A-Z > " + path) == 0)',
                '    with open(path, "r") as handle:',
                "        print(handle.read())",
                "    os.remove(path)",
                '    print(os.system("this-command-does-not-exist-503") == 127 * 256)',
                "    return 0",
                "",
            ]
        ),
    )


def test_system_call_sites_match_cpython(tmp_path: Path) -> None:
    """A module-level call and a call inside a loop body answer the same way."""
    _oracle(
        tmp_path,
        "system_sites.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    total = 0",
                "    for n in [0, 1, 2]:",
                '        total = total + os.system("exit " + str(n))',
                "    print(total)",
                "    return 0",
                "",
            ]
        ),
    )


def test_system_fence_shapes() -> None:
    """A wrong argument type or arity stays the checker's fence below the
    preflight; the lowerer's own fence is the type it cannot prove."""
    _fence(
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    os.system()",
                "    return 0",
                "",
            ]
        ),
        "PS2003",
        "os.system expects 1 argument(s)",
    )
