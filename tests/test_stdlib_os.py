"""os and os.path: the script-shaped surface.

`os.path.expanduser`, `expandvars`, `realpath`, `relpath`, `getsize`,
`getmtime`, `getatime`, `islink` and `splitdrive` join the fixed rows, and
`os.getpid`, `os.access`, `os.chdir`, `os.chmod`, `os.mkdir`, `os.replace`
and `os.unlink` join the `os` rows. Each probe diffs stdout against the
CPython oracle; the paths that depend on the machine (home, cwd) are
normalized by printing a boolean instead of the text.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_os_path_expanduser(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "expanduser.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    home: str = os.path.expanduser('~')",
                "    print(len(home) > 0)",
                "    print(os.path.expanduser('~').startswith('/'))",
                "    print(os.path.expanduser('~/x').startswith(home))",
                "    print(os.path.expanduser('/tmp/plain'))",
                "    print(os.path.expanduser('relative'))",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_path_expandvars(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "expandvars.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    print(os.path.expandvars('$HOME'))",
                "    print(os.path.expandvars('${HOME}/x'))",
                "    print(os.path.expandvars('a$HOME'))",
                "    print(os.path.expandvars('$$'))",
                "    print(os.path.expandvars('plain'))",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_path_relpath(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "relpath.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    print(os.path.relpath('/a/b/c', '/a'))",
                "    print(os.path.relpath('/a/b/c', '/a/b'))",
                "    print(os.path.relpath('/a/b/c', '/a/b/c'))",
                "    print(os.path.relpath('/a/b', '/a/b'))",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_path_stats_and_realpath(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "stats.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    print(os.path.getsize('/etc/hosts') > 0)",
                "    print(os.path.getmtime('/etc/hosts') > 0)",
                "    print(os.path.getatime('/etc/hosts') > 0)",
                "    print(os.path.islink('/etc/hosts'))",
                "    print(os.path.islink('/etc'))",
                "    print(os.path.realpath('/etc/../etc/hosts'))",
                "    print(os.path.splitdrive('/a/b')[0])",
                "    print(os.path.splitdrive('/a/b')[1])",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_access_and_getpid(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "access.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    print(os.access('/etc/hosts', os.R_OK))",
                "    print(os.access('/etc/hosts', os.W_OK))",
                "    print(os.getpid() > 0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_mkdir_chmod_replace_unlink(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import os",
            "import tempfile",
            "",
            "def run() -> int:",
            "    d: str = tempfile.mkdtemp()",
            "    p: str = os.path.join(d, 'a.txt')",
            "    with open(p, 'w') as handle:",
            "        handle.write('x')",
            "    os.mkdir(os.path.join(d, 'sub'))",
            "    print(os.path.isdir(os.path.join(d, 'sub')))",
            "    os.replace(p, os.path.join(d, 'b.txt'))",
            "    print(os.path.exists(os.path.join(d, 'b.txt')))",
            "    os.chmod(os.path.join(d, 'b.txt'), 0o644)",
            "    print(os.access(os.path.join(d, 'b.txt'), os.R_OK))",
            "    os.unlink(os.path.join(d, 'b.txt'))",
            "    print(os.path.exists(os.path.join(d, 'b.txt')))",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "mkdir.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    # The mkdtemp path differs between the two runs, so the probe prints
    # only booleans; the CPython oracle runs the same source.
    assert got.stdout == oracle(path, "run")


def test_os_chdir_returns_to_the_start(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "chdir.py",
        "\n".join(
            [
                "import os",
                "",
                "def run() -> int:",
                "    here: str = os.getcwd()",
                "    os.chdir('/tmp')",
                "    print(os.getcwd() != here)",
                "    os.chdir(here)",
                "    print(os.getcwd() == here)",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_path_realpath_missing_tail(tmp_path: Path) -> None:
    """#714: realpath on a missing target returns the path, not an error.

    posixpath.realpath is non-strict by default: a missing component is
    appended to the resolved prefix instead of raising. The compiled
    program must print the same string the CPython oracle prints.
    """
    _oracle(
        tmp_path,
        "realpath_missing.py",
        "\n".join(
            [
                "import os.path",
                "",
                "def run() -> int:",
                "    print(os.path.realpath('/no/such/file/xyz'))",
                "    print(os.path.realpath('/no/such/file/xyz/../tail'))",
                "    print(os.path.realpath('/etc/hosts/missing'))",
                "    print(os.path.realpath('/etc'))",
                "    print(os.path.realpath('/etc/../etc/hosts'))",
                "    print(os.path.realpath('/..'))",
                "    print(os.path.realpath('/a/../../..'))",
                "    print(os.path.realpath('.'))",
                "    print(os.path.realpath('..'))",
                "    return 0",
                "",
            ]
        ),
    )


def test_os_path_realpath_symlinks(tmp_path: Path) -> None:
    """#714: realpath resolves symlinks and keeps a missing tail.

    The fixture tree is built by the test (the lowered os surface has no
    symlink call), so both the compiled program and the oracle walk the
    same tree.
    """
    root = tmp_path / "tree"
    (root / "real" / "sub").mkdir(parents=True)
    (root / "real" / "f.txt").write_text("x", encoding="utf-8")
    os.symlink(root / "real", root / "abs")
    os.symlink("real", root / "rel")
    os.symlink("..", root / "real" / "up")
    source = "\n".join(
        [
            "import os.path",
            "",
            "def run() -> int:",
            f"    root: str = {str(root)!r}",
            "    print(os.path.realpath(root + '/abs/f.txt'))",
            "    print(os.path.realpath(root + '/rel/f.txt'))",
            "    print(os.path.realpath(root + '/abs/missing/tail'))",
            "    print(os.path.realpath(root + '/nope'))",
            "    print(os.path.realpath(root + '/real/../real/sub'))",
            "    return 0",
            "",
        ]
    )
    path = write_case(tmp_path, "realpath_links.py", source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
