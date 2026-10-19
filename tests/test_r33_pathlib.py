"""R33 pathlib rest: ``iterdir``/``glob``/``mkdir``/``with_name`` and ``p / 'x'``.

``Path.iterdir()`` and ``Path.glob(pattern)`` return R24 iterator values:
the runtime materializes a ``list[Path]`` (a seq of ``PsPath *`` like every
other class list) and the lowerer wraps it in the ``{seq, idx}`` cursor
record, so ``for``/``next``/``list`` work unchanged. ``mkdir`` lowers
``mode``/``parents``/``exist_ok``; ``with_name`` replaces the basename;
``p / x`` is ``__truediv__``/``__rtruediv__`` over str and Path operands.

Oracle diffs only print entry names: ``tempfile.mkdtemp`` paths differ
between the compiled run and the CPython run. ``mkdir`` failure raises
RuntimeError here where CPython raises FileExistsError/OSError, matching
``os.makedirs`` — only the non-raising shapes are oracle-diffed.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import oracle, run_cli, write_case


def _oracle_diff(tmp_path: Path, name: str, source: str, func: str = "probe") -> None:
    path = write_case(tmp_path, name, source)
    out = str(tmp_path / name.removesuffix(".py"))
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", func, "-o", out)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def _fence(tmp_path: Path, name: str, source: str, code: str, needle: str) -> None:
    path = write_case(tmp_path, name, source)
    got = run_cli("compile", str(path), "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "x"))
    assert got.returncode != 0
    assert code in got.stderr, got.stderr
    assert needle in got.stderr, got.stderr


def _raises(tmp_path: Path, name: str, source: str, needle: str) -> None:
    path = write_case(tmp_path, name, source)
    got = run_cli("compile", str(path), "--run", "--emit", "bin", "--call", "probe", "-o", str(tmp_path / "x"))
    assert got.returncode == 1, got.stderr
    assert needle in got.stderr, got.stderr


def test_r33_iterdir_for_loop(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_iterdir.py",
        "\n".join(
            [
                "import pathlib",
                "import tempfile",
                "",
                "def probe() -> str:",
                "    d: str = tempfile.mkdtemp()",
                "    (pathlib.Path(d) / 'a.txt').write_text('x')",
                "    (pathlib.Path(d) / 'b.log').write_text('y')",
                "    (pathlib.Path(d) / 'sub').mkdir()",
                "    names: list[str] = []",
                "    for p in pathlib.Path(d).iterdir():",
                "        names.append(p.name)",
                "    return ','.join(sorted(names))",
                "",
            ]
        ),
    )


def test_r33_truediv_forms(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_truediv.py",
        "\n".join(
            [
                "import pathlib",
                "",
                "def probe() -> str:",
                "    a: pathlib.Path = pathlib.Path('base') / 'sub' / 'leaf.txt'",
                "    b: pathlib.Path = pathlib.Path('base') / pathlib.Path('sub2')",
                "    c: pathlib.Path = 'left' / pathlib.Path('right')",
                "    return a.as_posix() + '|' + b.as_posix() + '|' + c.as_posix()",
                "",
            ]
        ),
    )


def test_r33_with_name(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_withname.py",
        "\n".join(
            [
                "import pathlib",
                "",
                "def probe() -> str:",
                "    a: pathlib.Path = pathlib.Path('dir/f.txt').with_name('g.log')",
                "    b: pathlib.Path = pathlib.Path('f.txt').with_name('g.txt')",
                "    return a.as_posix() + '|' + b.as_posix()",
                "",
            ]
        ),
    )


def test_r33_mkdir(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_mkdir.py",
        "\n".join(
            [
                "import pathlib",
                "import tempfile",
                "",
                "def probe() -> str:",
                "    d: str = tempfile.mkdtemp()",
                "    one: pathlib.Path = pathlib.Path(d) / 'one'",
                "    one.mkdir()",
                "    deep: pathlib.Path = pathlib.Path(d) / 'x' / 'y' / 'z'",
                "    deep.mkdir(parents=True)",
                "    deep.mkdir(parents=True, exist_ok=True)",
                "    one.mkdir(mode=0o700, exist_ok=True)",
                "    return str(one.is_dir()) + '|' + str(deep.is_dir())",
                "",
            ]
        ),
    )


def test_r33_glob_patterns(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_glob.py",
        "\n".join(
            [
                "import pathlib",
                "import tempfile",
                "",
                "def probe() -> str:",
                "    d: str = tempfile.mkdtemp()",
                "    (pathlib.Path(d) / 'a.txt').write_text('x')",
                "    (pathlib.Path(d) / 'b.log').write_text('y')",
                "    (pathlib.Path(d) / '.hidden').write_text('h')",
                "    (pathlib.Path(d) / 'sub').mkdir()",
                "    (pathlib.Path(d) / 'sub' / 'c.txt').write_text('z')",
                "    star: list[str] = []",
                "    for g in pathlib.Path(d).glob('*'):",
                "        star.append(g.name)",
                "    flat: list[str] = []",
                "    for g in pathlib.Path(d).glob('*.txt'):",
                "        flat.append(g.name)",
                "    sub: list[str] = []",
                "    for g in pathlib.Path(d).glob('sub/*.txt'):",
                "        sub.append(g.name)",
                "    deep: list[str] = []",
                "    for g in pathlib.Path(d).glob('**/*.txt'):",
                "        deep.append(g.name)",
                "    return (','.join(sorted(star)) + '|' + ','.join(sorted(flat))",
                "        + '|' + ','.join(sorted(sub)) + '|' + ','.join(sorted(deep)))",
                "",
            ]
        ),
    )


def test_r33_glob_starstar_dirs(tmp_path: Path) -> None:
    _oracle_diff(
        tmp_path,
        "r33_starstar.py",
        "\n".join(
            [
                "import pathlib",
                "import tempfile",
                "",
                "def probe() -> str:",
                "    d: str = tempfile.mkdtemp()",
                "    (pathlib.Path(d) / 'sub' / 'inner').mkdir(parents=True)",
                "    hits: list[str] = []",
                "    for g in pathlib.Path(d).glob('**/inner'):",
                "        hits.append(g.name)",
                "    return ','.join(sorted(hits))",
                "",
            ]
        ),
    )


def test_r33_raises(tmp_path: Path) -> None:
    cases = [
        (
            "r33_wn_root.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('/').with_name('x')\n    return 0\n",
            "ValueError",
        ),
        (
            "r33_wn_slash.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('a').with_name('x/y')\n    return 0\n",
            "ValueError",
        ),
        (
            "r33_wn_empty.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('a').with_name('')\n    return 0\n",
            "ValueError",
        ),
        (
            "r33_glob_empty.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('.').glob('')\n    return 0\n",
            "ValueError",
        ),
        (
            "r33_glob_badstar.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('.').glob('a**')\n    return 0\n",
            "ValueError",
        ),
    ]
    for name, source, needle in cases:
        _raises(tmp_path, name, source, needle)


def test_r33_fences_stay_closed(tmp_path: Path) -> None:
    cases = [
        (
            "r33_div_int.py",
            "import pathlib\n\ndef probe() -> int:\n    q: pathlib.Path = pathlib.Path('a') / 3\n    return 0\n",
            "unsupported-operation",
            "is not supported between `Path` and",
        ),
        (
            "r33_iterdir_arg.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('.').iterdir(1)\n    return 0\n",
            "bad-argument-count",
            "Expected 0 positional arguments",
        ),
        (
            "r33_glob_int.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('.').glob(3)\n    return 0\n",
            "bad-argument-type",
            "is not assignable to parameter `pattern`",
        ),
        (
            "r33_mkdir_kw.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('d').mkdir(nope=1)\n    return 0\n",
            "unexpected-keyword",
            "Unexpected keyword argument `nope`",
        ),
        (
            "r33_mkdir_flag.py",
            "import pathlib\n\ndef probe() -> int:\n    pathlib.Path('d').mkdir(parents=1)\n    return 0\n",
            "bad-argument-type",
            "is not assignable to parameter `parents`",
        ),
    ]
    for name, source, code, needle in cases:
        _fence(tmp_path, name, source, code, needle)
