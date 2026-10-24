"""shutil: the script-shaped surface.

`shutil.copy`, `copy2`, `copytree` and `move` join the fixed rows, and
`get_terminal_size` returns the `TerminalSize` record with its `columns`
and `lines` fields. Each probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_shutil_copyfile(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "copyfile.py",
        "\n".join(
            [
                "import os",
                "import shutil",
                "import tempfile",
                "",
                "def run() -> int:",
                "    d: str = tempfile.mkdtemp()",
                "    src: str = os.path.join(d, 'src.txt')",
                "    with open(src, 'w') as handle:",
                "        handle.write('first copy content line\\n')",
                "    dst: str = os.path.join(d, 'dst.txt')",
                "    copied: str = shutil.copyfile(src, dst)",
                "    print(copied == dst)",
                "    print(os.path.basename(copied))",
                "    with open(dst, 'r') as handle:",
                "        print(handle.read().strip())",
                "",
                "    # Empty file copy",
                "    empty_src: str = os.path.join(d, 'empty.txt')",
                "    with open(empty_src, 'w') as handle:",
                "        pass",
                "    empty_dst: str = os.path.join(d, 'empty_dst.txt')",
                "    copied_empty: str = shutil.copyfile(empty_src, empty_dst)",
                "    print(copied_empty == empty_dst)",
                "    print(os.path.getsize(empty_dst))",
                "",
                "    # Multi-kilobyte buffered copy",
                "    big_src: str = os.path.join(d, 'big.txt')",
                "    with open(big_src, 'wb') as handle:",
                "        handle.write(b'Z' * 300000)",
                "    big_dst: str = os.path.join(d, 'big_dst.txt')",
                "    copied_big: str = shutil.copyfile(big_src, big_dst)",
                "    print(copied_big == big_dst)",
                "    print(os.path.getsize(big_dst))",
                "",
                "    # Same-file error handling (identical path)",
                "    try:",
                "        shutil.copyfile(src, src)",
                "        print('samefile failed to raise')",
                "    except Exception as e:",
                "        ename1: str = type(e).__name__",
                "        print(ename1 == 'SameFileError' or ename1 == 'RuntimeError')",
                "",
                "    # Same-file error handling (different paths pointing to same file)",
                "    try:",
                "        shutil.copyfile(src, os.path.join(d, '.', 'src.txt'))",
                "        print('samefile dev/ino failed to raise')",
                "    except Exception as e:",
                "        ename2: str = type(e).__name__",
                "        print(ename2 == 'SameFileError' or ename2 == 'RuntimeError')",
                "",
                "    # Missing source file error handling",
                "    try:",
                "        shutil.copyfile(os.path.join(d, 'nonexistent.txt'), os.path.join(d, 'out.txt'))",
                "        print('missing failed to raise')",
                "    except Exception as e:",
                "        ename3: str = type(e).__name__",
                "        print(ename3 == 'FileNotFoundError' or ename3 == 'RuntimeError')",
                "",
                "    # Destination is directory error handling",
                "    try:",
                "        shutil.copyfile(src, d)",
                "        print('dst dir failed to raise')",
                "    except Exception as e:",
                "        ename4: str = type(e).__name__",
                "        print(ename4 == 'IsADirectoryError' or ename4 == 'RuntimeError')",
                "",
                "    return 0",
                "",
            ]
        ),
    )


def test_shutil_copy_and_copy2(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "copy.py",
        "\n".join(
            [
                "import os",
                "import shutil",
                "import tempfile",
                "",
                "def run() -> int:",
                "    d: str = tempfile.mkdtemp()",
                "    src: str = os.path.join(d, 'a.txt')",
                "    with open(src, 'w') as handle:",
                "        handle.write('hello')",
                "    dst: str = shutil.copy(src, os.path.join(d, 'b.txt'))",
                "    print(os.path.basename(dst))",
                "    with open(dst, 'r') as handle:",
                "        print(handle.read())",
                "    two: str = shutil.copy2(src, os.path.join(d, 'c.txt'))",
                "    print(os.path.basename(two))",
                "    print(os.path.getsize(two))",
                "    return 0",
                "",
            ]
        ),
    )


def test_shutil_copytree_and_move(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "tree.py",
        "\n".join(
            [
                "import os",
                "import shutil",
                "import tempfile",
                "",
                "def run() -> int:",
                "    d: str = tempfile.mkdtemp()",
                "    tree: str = os.path.join(d, 'tree')",
                "    os.makedirs(os.path.join(tree, 'sub'))",
                "    with open(os.path.join(tree, 'sub', 'x.txt'), 'w') as handle:",
                "        handle.write('x')",
                "    copied: str = shutil.copytree(tree, os.path.join(d, 'tree2'))",
                "    print(os.path.exists(os.path.join(copied, 'sub', 'x.txt')))",
                "    moved: str = shutil.move(os.path.join(copied, 'sub', 'x.txt'), os.path.join(copied, 'y.txt'))",
                "    print(os.path.basename(moved))",
                "    print(os.path.exists(os.path.join(copied, 'sub', 'x.txt')))",
                "    return 0",
                "",
            ]
        ),
    )


def test_shutil_get_terminal_size(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "terminal.py",
        "\n".join(
            [
                "import shutil",
                "import os",
                "",
                "def run() -> int:",
                "    size: os.terminal_size = shutil.get_terminal_size()",
                "    print(size.columns > 0)",
                "    print(size.lines > 0)",
                "    return 0",
                "",
            ]
        ),
    )
