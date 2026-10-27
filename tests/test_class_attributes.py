"""A class-body constant assignment folds into a class attribute.

`class C: LIMIT = 10` is a class attribute, not a field: the value lives on
the class, not on the instances. The lowering folds it at every read site
(`C.LIMIT` and `c.LIMIT`), so the class layout never grows a slot. Every
other class-body assignment keeps its fence.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pyscriptc import cli

needs_cc = pytest.mark.skipif(
    shutil.which("clang") is None and shutil.which("zig") is None,
    reason="clang or zig required",
)

CONSTANTS = """
class Config:
    base: int
    MAX = 10
    NAME = "cfg"
    RATIO = 0.5
    SEP = b"-"
    FLAG = True
    EMPTY = None

    def __init__(self, base: int) -> None:
        self.base = base

    def scaled(self) -> int:
        return self.base * Config.MAX


def probe() -> int:
    c = Config(3)
    print(Config.MAX)
    print(Config.NAME)
    print(Config.RATIO)
    print(Config.SEP)
    print(Config.FLAG)
    print(str(Config.EMPTY))
    print(c.MAX)
    print(c.scaled())
    return 0


if __name__ == "__main__":
    probe()
"""

INHERITED = """
LIMIT = 7


class Base:
    LIMIT = LIMIT


class Child(Base):
    OWN = 1


def probe() -> int:
    print(Base.LIMIT)
    print(Child.LIMIT)
    print(Child.OWN)
    return 0


if __name__ == "__main__":
    probe()
"""


def _script(tmp_path: Path, text: str, name: str = "prog.py") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@needs_cc
def test_class_attribute_constants_match_cpython(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, CONSTANTS)
    code = cli.main(["verify", str(source), "--skip-check", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0, receipt
    assert receipt["match"] is True
    stdout = next(check for check in receipt["checks"] if check["name"] == "stdout")
    assert stdout["match"] is True
    assert stdout["nativeBytes"] == len("10\ncfg\n0.5\nb'-'\nTrue\nNone\n10\n30\n")


@needs_cc
def test_class_attribute_inherits_through_the_mro(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _script(tmp_path, INHERITED)
    code = cli.main(["verify", str(source), "--skip-check", "--json"])
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0, receipt
    assert receipt["match"] is True
    stdout = next(check for check in receipt["checks"] if check["name"] == "stdout")
    assert stdout["nativeBytes"] == len("7\n7\n1\n")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("class C:\n    x = unknown_name()\n", "a class attribute must be a constant (x)"),
        ("class C:\n    x = [1, 2]\n", "a class attribute must be a constant (x)"),
        ("class C:\n    x, y = 1, 2\n", "Assign in a class body is not lowered"),
        ("class C:\n    x: int = 5\n", "class fields are declared with an annotation and no value"),
    ],
)
def test_other_class_body_assignments_stay_fenced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str, message: str
) -> None:
    path = _script(tmp_path, source)
    code = cli.main(["coverage", str(path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    messages = [fence["message"] for fence in report["fences"]]
    assert message in messages, messages
