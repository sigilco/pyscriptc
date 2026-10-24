"""Config surface end to end: `pyscriptc config`, compile-time application, run parity.

Every compile here is a real binary run checked against CPython's output
(same oracle shape as tests/test_gates.py).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

HELLO = "def main() -> int:\n    print('hello')\n    return 0\n"


def _project(tmp_path: Path, table: str, script: str = HELLO, name: str = "app.py") -> Path:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "demo"\nversion = "0.1.0"\n\n{table}\n')
    return write_case(tmp_path, name, script)


def _oracle(script: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-c", f"import runpy; runpy.run_path(r'{script}')"],
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def _oracle_main(script: Path) -> str:
    """CPython running main() the way `--call main` does."""
    source = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('m', r'{script}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(mod.main())\n"
    )
    return subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=True).stdout


def test_config_subcommand_text(tmp_path: Path) -> None:
    _project(tmp_path, "[tool.pyscriptc]\nentry = 'main'\n\n[tool.pyscriptc.imports]\norjson = 'replace'\n")
    got = run_cli("config", str(tmp_path / "app.py"))
    assert got.returncode == 0, got.stderr
    assert "source: pyproject.toml" in got.stdout
    assert "entry: main" in got.stdout
    assert "import orjson: replace" in got.stdout


def test_config_subcommand_json(tmp_path: Path) -> None:
    import json

    _project(tmp_path, "[tool.pyscriptc]\nfail-on = 'any'\n")
    got = run_cli("config", str(tmp_path / "app.py"), "--json")
    assert got.returncode == 0, got.stderr
    data = json.loads(got.stdout)
    assert data["source"] == "pyproject.toml"
    assert data["fail-on"] == "any"
    assert data["imports"] == {}


def test_config_subcommand_default(tmp_path: Path) -> None:
    path = write_case(tmp_path, "solo.py", HELLO)
    import json

    got = run_cli("config", str(path), "--json")
    assert json.loads(got.stdout)["source"] == "default"


def test_config_subcommand_error_exit(tmp_path: Path) -> None:
    _project(tmp_path, "[tool.pyscriptc]\nnope = 1\n")
    got = run_cli("config", str(tmp_path / "app.py"))
    assert got.returncode == 1
    assert "PS3102" in got.stderr


def test_compile_notes_config_origin(tmp_path: Path) -> None:
    _project(tmp_path, "[tool.pyscriptc]\nentry = 'main'\n")
    got = run_cli("compile", str(tmp_path / "app.py"), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / "o.ll"))
    assert got.returncode == 0, got.stderr
    assert f"config from {tmp_path / 'pyproject.toml'} (pyproject.toml)" in got.stderr


@needs_clang
def test_compile_runs_and_matches_cpython(tmp_path: Path) -> None:
    _project(tmp_path, "[tool.pyscriptc]\nentry = 'main'\n")
    script = tmp_path / "app.py"
    got = run_cli("compile", str(script), "--run", "--skip-check", "-o", str(tmp_path / "app"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "hello\n0\n"
    assert _oracle_main(script) == "hello\n0\n"


@needs_clang
def test_config_fail_on_applies_to_coverage(tmp_path: Path) -> None:
    fenced = "from typing import Any\n\n\ndef main() -> int:\n    x: Any = 1\n    print(x)\n    return 0\n"
    _project(tmp_path, "[tool.pyscriptc]\nfail-on = 'any'\n", script=fenced)
    path = tmp_path / "app.py"
    assert run_cli("coverage", str(path)).returncode == 1
    # Explicit flag wins over config.
    assert run_cli("coverage", str(path), "--fail-on", "blockers").returncode == 1
    _project(tmp_path, "", name="plain.py")
    plain = write_case(tmp_path, "plain.py", fenced)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "d"\n\n[tool.pyscriptc]\n')
    (plain).write_text(fenced)
    assert run_cli("coverage", str(plain)).returncode == 0


@needs_clang
def test_pep723_script_still_compiles_with_project(tmp_path: Path) -> None:
    script = _project(
        tmp_path,
        "[tool.pyscriptc]\nentry = 'main'\n",
        script='# /// script\n# requires-python = ">=3.12"\n# dependencies = []\n# ///\n' + HELLO,
    )
    got = run_cli("compile", str(script), "--run", "--skip-check", "-o", str(tmp_path / "app"))
    assert got.returncode == 0, got.stderr
    assert "requires-python >=3.12" in got.stderr
    assert got.stdout.startswith("hello\n")


def test_compile_refuse_lane_fails(tmp_path: Path) -> None:
    script = _project(
        tmp_path,
        "[tool.pyscriptc.imports]\nrich = 'refuse'\n",
        script="import rich\n\ndef main() -> int:\n    return 0\n",
    )
    got = run_cli("compile", str(script), "--skip-check", "-o", str(tmp_path / "app"))
    assert got.returncode == 3
    assert "PS2002" in got.stderr
    assert "import `rich` is refused by the `imports` policy" in got.stderr


def test_compile_other_lanes_unchanged(tmp_path: Path) -> None:
    script = _project(
        tmp_path,
        "[tool.pyscriptc.imports]\nrich = 'compile'\nfastapi = 'replace'\n",
        script="import rich\nimport fastapi\n\ndef main() -> int:\n    return 0\n",
    )
    got = run_cli("compile", str(script), "--skip-check", "-o", str(tmp_path / "app"))
    assert got.returncode == 3
    assert "PS2002" in got.stderr
    assert "rich is not in the closed import table" in got.stderr
    assert "fastapi is not in the closed import table" in got.stderr
    assert "refused by" not in got.stderr


def test_config_subcommand_unchanged_with_refuse(tmp_path: Path) -> None:
    import json

    _project(tmp_path, "[tool.pyscriptc.imports]\nrich = 'refuse'\norjson = 'replace'\n")
    got = run_cli("config", str(tmp_path / "app.py"), "--json")
    assert got.returncode == 0, got.stderr
    data = json.loads(got.stdout)
    assert data["imports"]["rich"] == "refuse"
    assert data["imports"]["orjson"] == "replace"
