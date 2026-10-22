"""urllib.parse.SplitResult is usable as an annotation and an import (issue #222)."""

from __future__ import annotations

from pathlib import Path

from tests.test_e1_del import oracle_script
from tests.test_gates import run_cli, write_case

ATTR = """import urllib.parse


def main() -> int:
    parts: urllib.parse.SplitResult = urllib.parse.urlsplit("http://h.com/a?b=1#c")
    print(parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment)
    return 0


if __name__ == "__main__":
    main()
"""

FROM = """from urllib.parse import SplitResult, urlsplit


def host(p: SplitResult) -> str:
    return p.netloc


def main() -> int:
    q: SplitResult = urlsplit("https://x.org/y")
    print(host(q), q.path)
    return 0


if __name__ == "__main__":
    main()
"""


def _check(tmp_path: Path, name: str, source: str) -> None:
    path = write_case(tmp_path, f"{name}.py", source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "bin", "-o", str(tmp_path / name), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle_script(path)


def test_attribute_annotation(tmp_path: Path) -> None:
    _check(tmp_path, "attr", ATTR)


def test_from_import_annotation(tmp_path: Path) -> None:
    _check(tmp_path, "fromimp", FROM)
