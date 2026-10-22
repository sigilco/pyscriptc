"""The declared `requires-python` floor must be able to parse every file the wheel ships.

Regression for the v0.4.1 install break: `requires-python = ">=3.10"` while the source used the
3.14-only unparenthesized `except A, B:` (PEP 758), so installs on 3.10 to 3.13 died with a SyntaxError.
"""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


def _declared_floor() -> tuple[int, int]:
    spec = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    m = re.fullmatch(r">=\s*(\d+)\.(\d+)(?:\.\d+)?", spec.strip())
    assert m, f"requires-python must be a plain '>=X.Y' floor, got {spec!r}"
    return int(m[1]), int(m[2])


def test_every_src_file_parses_at_declared_floor():
    floor = _declared_floor()
    failures = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=floor)
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    assert not failures, (
        f"requires-python floor {floor} is lower than the syntax the source needs; "
        "raise the floor or rewrite the syntax:\n" + "\n".join(failures)
    )


def test_floor_matches_ruff_target_version():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    target = cfg["tool"]["ruff"]["target-version"]
    major, minor = _declared_floor()
    assert target == f"py{major}{minor}", "ruff target-version must equal the requires-python floor"
