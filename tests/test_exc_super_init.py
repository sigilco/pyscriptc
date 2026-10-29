"""`super().__init__` in a user exception class (issue #814).

A user exception class whose `__init__` chains the builtin base with
`super().__init__(msg)` lowers natively: the class's message is the
chained expression, evaluated at the raise site with the ctor arguments
bound, exactly as CPython reads it. Every case diffs the compiled binary
against CPython.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args], cwd=ROOT, capture_output=True, text=True, timeout=300
    )


def _compare(tmp_path: Path, source: str, name: str = "case.py") -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / "bin"
    comp = _cli("compile", str(path), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert (got.returncode, got.stdout, got.stderr) == (want.returncode, want.stdout, want.stderr)
    return got.stdout


def test_super_init_message_is_the_chained_expression(tmp_path: Path) -> None:
    """`super().__init__(f"app: {message}")` is the class's message."""

    out = _compare(
        tmp_path,
        """
class AppErr(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"app: {message}")


def main() -> int:
    try:
        raise AppErr("boom")
    except AppErr as exc:
        print(str(exc))
    return 0


if __name__ == "__main__":
    main()
""",
    )
    assert out == "app: boom\n"


def test_super_init_plain_message(tmp_path: Path) -> None:
    """`super().__init__(message)` is the message verbatim."""

    out = _compare(
        tmp_path,
        """
class AppErr(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def main() -> int:
    try:
        raise AppErr("boom")
    except AppErr as exc:
        print(str(exc))
    return 0


if __name__ == "__main__":
    main()
""",
    )
    assert out == "boom\n"


def test_super_init_chains_through_a_user_base(tmp_path: Path) -> None:
    """The chain walks the MRO: the base's message expression wins."""

    out = _compare(
        tmp_path,
        """
class SpikeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


class IRError(SpikeError):
    def __init__(self, message: str) -> None:
        super().__init__("PS0001", message)


def main() -> int:
    try:
        raise IRError("bad thing")
    except SpikeError as exc:
        print(str(exc))
    return 0


if __name__ == "__main__":
    main()
""",
    )
    assert out == "PS0001: bad thing\n"


def test_super_init_uncaught_reports_the_chained_message(tmp_path: Path) -> None:
    """An uncaught raise reports the chained message, not the raw argument.

    A compiled binary prints no traceback (divergence 1), so the last
    stderr line is the comparison.
    """

    source = """
class AppErr(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"app: {message}")


def main() -> int:
    raise AppErr("boom")


if __name__ == "__main__":
    main()
"""
    path = tmp_path / "uncaught.py"
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / "bin"
    comp = _cli("compile", str(path), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    got = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    want = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert got.returncode == want.returncode == 1
    assert got.stderr.strip().splitlines()[-1] == want.stderr.strip().splitlines()[-1]
    assert got.stderr.strip().splitlines()[-1] == "AppErr: app: boom"


def test_super_init_on_a_plain_class_still_lowers(tmp_path: Path) -> None:
    """`super().__init__` on a user base keeps its existing lowering."""

    out = _compare(
        tmp_path,
        """
class Base:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Child(Base):
    def __init__(self, x: int) -> None:
        super().__init__(x)


def main() -> int:
    print(Child(3).x)
    return 0


if __name__ == "__main__":
    main()
""",
    )
    assert out == "3\n"


def test_super_init_with_two_arguments_refuses(tmp_path: Path) -> None:
    """The builtin base takes one message argument; two refuse."""

    path = tmp_path / "two.py"
    path.write_text(
        """
class AppErr(Exception):
    def __init__(self, message: str) -> None:
        super().__init__("a", "b")


def main() -> int:
    raise AppErr("boom")
""",
        encoding="utf-8",
    )
    comp = _cli("compile", str(path), "--emit", "ir", "-o", str(tmp_path / "out"), cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS2003" in comp.stderr
