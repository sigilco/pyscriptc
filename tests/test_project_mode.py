"""Project mode: one binary per `[project.scripts]` entry point.

The oracle is CPython itself: importing the entry module and calling the
attribute, then exiting with its return value, must produce the same stdout
and exit code as the built binary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli

from pyscriptc.config import ConfigError, entry_points, project_roots


def write_project(tmp_path: Path, name: str, files: dict[str, str], pyproject: str) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root


def oracle_run(root: Path, module: str, attr: str) -> subprocess.CompletedProcess[str]:
    code = f"import sys\nimport {module}\nsys.exit({module}.{attr}())\n"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=root / "src",
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
    )


def build_script(root: Path, name: str, tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    return run_cli("build", str(root), "--script", script, "--skip-check", "--out-dir", str(tmp_path / "build"))


def assert_matches_cpython(
    root: Path, tmp_path: Path, script: str, module: str, attr: str, argv: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    got = build_script(root, script, tmp_path, script)
    assert got.returncode == 0, got.stderr
    binary = Path(got.stdout.strip().splitlines()[-1])
    expected = oracle_run(root, module, attr)
    actual = subprocess.run([str(binary), *(argv or [])], capture_output=True, text=True)
    assert actual.stdout == expected.stdout, f"stderr: {actual.stderr}\nexpected: {expected.stdout}"
    assert actual.returncode == expected.returncode
    return actual


SIMPLE_PYPROJECT = '[project]\nname = "mylib"\nversion = "0.1.0"\n\n[project.scripts]\nhello = "mylib.hello:main"\n'


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_script_build_matches_cpython(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "mylib",
        {
            "src/mylib/__init__.py": "NAME = 'mylib'\n",
            "src/mylib/hello.py": "from . import NAME\n\nprint('hello module')\n\n\n"
            "def main() -> int:\n    print('hello from', NAME)\n    return 0\n",
        },
        SIMPLE_PYPROJECT,
    )
    assert_matches_cpython(root, tmp_path, "hello", "mylib.hello", "main")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_exit_code_is_the_attribute_return_value(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "mylib",
        {
            "src/mylib/__init__.py": "",
            "src/mylib/hello.py": "def main() -> int:\n    return 7\n",
        },
        SIMPLE_PYPROJECT,
    )
    assert_matches_cpython(root, tmp_path, "hello", "mylib.hello", "main")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_two_scripts_build_independently(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "mylib",
        {
            "src/mylib/__init__.py": "NAME = 'mylib'\n",
            "src/mylib/hello.py": "from . import NAME\n\n\n"
            "def main() -> int:\n    print('hello', NAME)\n    return 7\n",
            "src/mylib/bye.py": "from .hello import main\n\n\ndef run() -> int:\n    main()\n    return 0\n",
        },
        '[project]\nname = "mylib"\nversion = "0.1.0"\n\n[project.scripts]\n'
        'hello = "mylib.hello:main"\nbye = "mylib.bye:run"\n',
    )
    assert_matches_cpython(root, tmp_path, "hello", "mylib.hello", "main")
    assert_matches_cpython(root, tmp_path, "bye", "mylib.bye", "run")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_package_module_entry(tmp_path: Path) -> None:
    # `app = "app:main"`: the package's own __init__ is the entry module.
    root = write_project(
        tmp_path,
        "app",
        {
            "src/app/__init__.py": "VERSION = '0.1'\n\n\ndef main() -> int:\n    print('app', VERSION)\n    return 0\n",
        },
        '[project]\nname = "app"\nversion = "0.1.0"\n\n[project.scripts]\napp = "app:main"\n',
    )
    assert_matches_cpython(root, tmp_path, "app", "app", "main")


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_sys_argv_reaches_the_binary(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "tool",
        {
            "src/tool/__init__.py": "",
            "src/tool/cli.py": "import sys\n\n\ndef main() -> int:\n    print('tool', len(sys.argv))\n    return 0\n",
        },
        '[project]\nname = "tool"\nversion = "0.1.0"\n\n[project.scripts]\ntool = "tool.cli:main"\n',
    )
    got = build_script(root, "tool", tmp_path, "tool")
    assert got.returncode == 0, got.stderr
    binary = Path(got.stdout.strip().splitlines()[-1])
    actual = subprocess.run([str(binary), "a", "b"], capture_output=True, text=True)
    assert actual.stdout == "tool 3\n"


def test_a_fenced_tree_refuses_with_the_fence(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "mylib",
        {
            "src/mylib/__init__.py": "",
            "src/mylib/hello.py": "from typing import Any\n\n\ndef main(x: Any) -> int:\n    return 1\n",
        },
        SIMPLE_PYPROJECT,
    )
    got = run_cli("build", str(root), "--skip-check")
    assert got.returncode == 3, got.stderr
    assert "PS1104" in got.stderr


def test_a_missing_module_names_the_script_and_roots(tmp_path: Path) -> None:
    root = write_project(tmp_path, "mylib", {"src/mylib/__init__.py": ""}, SIMPLE_PYPROJECT)
    got = run_cli("build", str(root))
    assert got.returncode != 0
    assert "mylib.hello not found under the project roots src" in got.stderr


def test_an_unknown_script_name_is_rejected(tmp_path: Path) -> None:
    root = write_project(tmp_path, "mylib", {"src/mylib/__init__.py": ""}, SIMPLE_PYPROJECT)
    got = run_cli("build", str(root), "--script", "nope")
    assert got.returncode == 2
    assert "no console script 'nope'" in got.stderr
    assert "known: hello" in got.stderr


def test_a_project_without_scripts_says_so(tmp_path: Path) -> None:
    root = write_project(tmp_path, "mylib", {"src/mylib/__init__.py": ""}, '[project]\nname = "mylib"\n')
    got = run_cli("build", str(root))
    assert got.returncode == 1
    assert "no [project.scripts] table" in got.stderr


def test_build_outside_a_project_refuses(tmp_path: Path) -> None:
    got = run_cli("build", str(tmp_path))
    assert got.returncode != 0
    assert "no pyproject.toml" in got.stderr


def test_entry_points_read_and_validate(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("[project]\nname = 'x'\n\n[project.scripts]\nhello = 'mylib.hello:main'\nflat = 'cli:run'\n")
    assert entry_points(path) == {"hello": "mylib.hello:main", "flat": "cli:run"}
    path.write_text("[project]\nname = 'x'\n")
    assert entry_points(path) == {}
    path.write_text("[project]\nname = 'x'\n\n[project.scripts]\nbad = 'nocolon'\n")
    with pytest.raises(ConfigError, match="module:attribute"):
        entry_points(path)
    path.write_text("[project]\nname = 'x'\n\n[project.scripts]\nbad = 'no-dash:main'\n")
    with pytest.raises(ConfigError, match="module path"):
        entry_points(path)
    path.write_text("[project]\nname = 'x'\n\n[project.scripts]\nbad = 'm:not an attr'\n")
    with pytest.raises(ConfigError, match="attribute name"):
        entry_points(path)


def test_project_roots_default_and_override(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    (tmp_path / "src").mkdir()
    path.write_text("[project]\nname = 'x'\n")
    assert project_roots(path) == ("src",)
    path.write_text("[project]\nname = 'x'\n\n[tool.pyscriptc]\nsrc = 'lib'\n")
    assert project_roots(path) == (".",)
    (tmp_path / "lib").mkdir()
    assert project_roots(path) == ("lib",)
    path.write_text("[project]\nname = 'x'\n\n[tool.pyscriptc]\nsrc = ['a', 'b']\n")
    (tmp_path / "b").mkdir()
    assert project_roots(path) == ("b",)


def test_config_reports_the_scripts(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        "mylib",
        {"src/mylib/__init__.py": "", "src/mylib/hello.py": ""},
        '[project]\nname = "mylib"\n\n[project.scripts]\nhello = "mylib.hello:main"\n',
    )
    got = run_cli("config", str(root), "--json")
    assert got.returncode == 0, got.stderr
    assert '"hello": "mylib.hello:main"' in got.stdout
    got = run_cli("config", str(root))
    assert "hello: mylib.hello:main" in got.stdout
