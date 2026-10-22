"""`pyscriptc run`: content-hash binary cache, argument pass-through, exit code."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pyscriptc import cli, runcache

needs_clang = pytest.mark.skipif(shutil.which("clang") is None, reason="clang required")

SCRIPT = (
    "import sys\n\n"
    "def main() -> int:\n"
    "    print('args', ' '.join(sys.argv[1:]))\n"
    "    sys.exit(7)\n"
    "    return 0\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)


@pytest.fixture(autouse=True)
def cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "cache"
    monkeypatch.setenv("PYSC_CACHE_DIR", str(home))
    monkeypatch.delenv("PYSC_NO_RUN_CACHE", raising=False)
    return home


def _script(tmp_path: Path, text: str = SCRIPT) -> Path:
    path = tmp_path / "prog.py"
    path.write_text(text)
    return path


@pytest.fixture
def compiles(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count real compiles while still running them."""
    seen: list[str] = []
    real = cli.compile_cmd

    def spy(args):  # type: ignore[no-untyped-def]
        seen.append(args.source)
        return real(args)

    monkeypatch.setattr(cli, "compile_cmd", spy)
    return seen


@needs_clang
def test_cache_hit_skips_compile_and_passes_args_and_exit_code(
    tmp_path: Path, compiles: list[str], capfd: pytest.CaptureFixture[str]
) -> None:
    src = _script(tmp_path)
    assert cli.main(["run", str(src), "--skip-check", "--", "a", "b"]) == 7
    assert "args a b" in capfd.readouterr().out
    assert len(compiles) == 1
    assert cli.main(["run", str(src), "--skip-check", "--", "c"]) == 7
    assert "args c" in capfd.readouterr().out
    assert len(compiles) == 1, "second run must reuse the cached binary"


@needs_clang
def test_source_change_recompiles(tmp_path: Path, compiles: list[str]) -> None:
    src = _script(tmp_path)
    assert cli.main(["run", str(src), "--skip-check"]) == 7
    src.write_text(SCRIPT.replace("sys.exit(7)", "sys.exit(3)"))
    assert cli.main(["run", str(src), "--skip-check"]) == 3
    assert len(compiles) == 2


@needs_clang
def test_no_cache_flag_always_compiles(tmp_path: Path, compiles: list[str]) -> None:
    src = _script(tmp_path)
    cli.main(["run", str(src), "--skip-check", "--no-cache"])
    cli.main(["run", str(src), "--skip-check", "--no-cache"])
    assert len(compiles) == 2


@needs_clang
def test_stdin_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]) -> None:
    src = _script(tmp_path, "def main() -> int:\n    print(input().upper())\n    return 0\n\nmain()\n")
    stdin = tmp_path / "in.txt"
    stdin.write_text("hello\n")
    with stdin.open() as handle:
        import os

        saved = os.dup(0)
        os.dup2(handle.fileno(), 0)
        try:
            assert cli.main(["run", str(src), "--skip-check"]) == 0
        finally:
            os.dup2(saved, 0)
            os.close(saved)
    assert capfd.readouterr().out.strip() == "HELLO"


def test_key_depends_on_flags_and_content(tmp_path: Path) -> None:
    src = _script(tmp_path)
    base = runcache.key_for(src, None, ["driver=clang"])
    assert base == runcache.key_for(src, None, ["driver=clang"])
    assert base != runcache.key_for(src, None, ["driver=zig"])
    src.write_text(SCRIPT + "# edit\n")
    assert base != runcache.key_for(src, None, ["driver=clang"])


def test_fenced_source_does_not_cache(tmp_path: Path, cache_home: Path) -> None:
    src = _script(tmp_path, "def main() -> int:\n    print([1])\n    return 0\n\nmain()\n")
    code = cli.main(["run", str(src), "--skip-check"])
    assert code != 0
    assert not list((cache_home / "bin").glob("*/*")) if (cache_home / "bin").exists() else True


def test_dashdash_needs_run_or_compile_run() -> None:
    with pytest.raises(SystemExit):
        cli.main(["coverage", "x.py", "--", "a"])
