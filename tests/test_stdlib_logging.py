"""logging: the script subset of the logger family.

`logging.getLogger(name)` returns a `Logger` whose record methods print to
stderr, and the module-level `debug`/`info`/`warning`/`error`/`critical`
stay on the root logger through the embedded-CPython island. With no
handler configured, CPython's last-resort stderr handler prints the bare
message; debug and info stay silent at the default WARNING level.

Each probe diffs stderr against the CPython oracle, and the fence cases
keep the rest of the module closed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import run_cli, write_case

ROOT = Path(__file__).resolve().parents[1]


def _run_both(tmp_path: Path, name: str, body: list[str]) -> tuple[str, str]:
    """Compile to a binary, run it and the CPython oracle, diff stderr."""
    path = write_case(tmp_path, name, "\n".join([*body, ""]))
    binary = tmp_path / path.stem
    got = run_cli("compile", str(path), "--emit", "bin", "-o", str(binary))
    assert got.returncode == 0, got.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True)
    oracle = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    return native.stderr, oracle.stderr


def test_logger_records_match_cpython(tmp_path: Path) -> None:
    native, oracle = _run_both(
        tmp_path,
        "logger.py",
        [
            "import logging",
            "",
            "",
            "def run() -> int:",
            "    log: logging.Logger = logging.getLogger('app.main')",
            "    log.debug('dbg')",
            "    log.info('inf')",
            "    log.warning('careful')",
            "    log.error('bad')",
            "    log.critical('worse')",
            "    return 0",
            "",
            'if __name__ == "__main__":',
            "    run()",
        ],
    )
    assert native == oracle
    assert native.splitlines() == ["careful", "bad", "worse"]


def test_logger_name_is_carried(tmp_path: Path) -> None:
    native, oracle = _run_both(
        tmp_path,
        "logname.py",
        [
            "import logging",
            "",
            "",
            "def run() -> int:",
            "    log: logging.Logger = logging.getLogger('worker')",
            "    log.warning('starting')",
            "    return 0",
            "",
            'if __name__ == "__main__":',
            "    run()",
        ],
    )
    assert native == oracle


def test_module_level_logging_island(tmp_path: Path) -> None:
    native, oracle = _run_both(
        tmp_path,
        "logmod.py",
        [
            "import logging",
            "",
            "",
            "def run() -> int:",
            "    logging.error('mod err')",
            "    logging.info('mod info')",
            "    return 0",
            "",
            'if __name__ == "__main__":',
            "    run()",
        ],
    )
    assert native == oracle
    assert native.splitlines() == ["ERROR:root:mod err"]


def test_logger_lowering_without_fences() -> None:
    from pyscriptc.lower import lower_source

    mod = lower_source(
        """import logging


def probe() -> int:
    log: logging.Logger = logging.getLogger('x')
    log.warning('w')
    log.error('e')
    log.exception('x')
    return 0
"""
    )
    assert mod["fences"] == []


def test_other_logging_names_stay_fenced(tmp_path: Path) -> None:
    cases = [
        ("config.py", "logging.basicConfig", "logging.basicConfig is not in the lowered subset of logging"),
        ("handler.py", "logging.Handler", "logging.Handler is not in the lowered subset of logging"),
        ("level.py", "log.setLevel(10)", "Logger.setLevel is not a method"),
    ]
    for name, expr, text in cases:
        source = (
            "import logging\n"
            "def probe() -> int:\n"
            "    log: logging.Logger = logging.getLogger('x')\n"
            f"    {expr}\n"
            "    return 1\n"
        )
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
        assert text in got.stderr, f"{name}\n{got.stderr}"


def test_math_isfinite_matches_cpython(tmp_path: Path) -> None:
    from tests.test_gates import compile_run, oracle, write_case

    source = "\n".join(
        [
            "import math",
            "",
            "",
            "def run() -> int:",
            "    total: int = 0",
            "    if math.isfinite(1.5):",
            "        total = total + 1",
            "    if math.isfinite(math.inf):",
            "        total = total + 10",
            "    if math.isfinite(math.nan):",
            "        total = total + 100",
            "    if math.isfinite(-2.5):",
            "        total = total + 1000",
            "    print(total)",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "fin.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
    assert got.stdout.splitlines()[0] == "1001"
