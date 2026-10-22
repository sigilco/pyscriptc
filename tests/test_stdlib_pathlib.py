"""pathlib: the script-shaped surface.

`Path.is_absolute`, `resolve`, `unlink`, `touch`, `rename`, `stat` and
`expanduser` join the method table, and `Path.cwd()` and `Path.home()` join
the module rows. Each probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_path_is_absolute_and_resolve(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "absolute.py",
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "def run() -> int:",
                "    print(Path('/a/b').is_absolute())",
                "    print(Path('a/b').is_absolute())",
                "    print(Path('/a/b').resolve().as_posix())",
                "    print(Path('/a/./b').resolve().as_posix())",
                "    print(Path('/a/b/../c').resolve().as_posix())",
                "    return 0",
                "",
            ]
        ),
    )


def test_path_cwd_and_home(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "cwd.py",
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "def run() -> int:",
                "    print(Path.cwd().is_absolute())",
                "    print(Path.cwd().as_posix() == Path.cwd().as_posix())",
                "    print(Path.home().is_absolute())",
                "    print(Path('~').expanduser().is_absolute())",
                "    return 0",
                "",
            ]
        ),
    )


def test_path_touch_unlink_rename(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "touch.py",
        "\n".join(
            [
                "import tempfile",
                "from pathlib import Path",
                "",
                "def run() -> int:",
                "    d: str = tempfile.mkdtemp()",
                "    p: Path = Path(d) / 'a.txt'",
                "    p.touch()",
                "    print(p.exists())",
                "    print(p.is_file())",
                "    q: Path = p.rename(str(Path(d) / 'b.txt'))",
                "    print(q.exists())",
                "    print(p.exists())",
                "    q.unlink()",
                "    print(q.exists())",
                "    return 0",
                "",
            ]
        ),
    )


def test_path_stat(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "stat.py",
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "def run() -> int:",
                "    print(Path('/etc/hosts').stat().st_size > 0)",
                "    print(Path('/etc').stat().st_size >= 0)",
                "    return 0",
                "",
            ]
        ),
    )
