"""The pydantic seam: a model program runs out of the box, no flags.

pydantic's Python layer cannot lower natively (the base of a model class is
an island member), but the island lane is the working boundary to
pydantic-core. The seam therefore has to work in the default lane: a program
that imports pydantic runs without `--fallback=cpython` and without an
`[imports]` table, and its output matches CPython's.

Refs the note `.agents/notes/2026-11-02-pydantic-support.md`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("pydantic")


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # The shell's VIRTUAL_ENV can be the empty string, which the island
    # interpreter treats as a real (broken) venv path; point it at the venv
    # the tests already run under.
    if not env.get("VIRTUAL_ENV"):
        env["VIRTUAL_ENV"] = sys.prefix
    return subprocess.run(
        [sys.executable, "-m", "pyscriptc", *args],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def _compare(tmp_path: Path, source_text: str, name: str = "prog") -> tuple[str, str]:
    """Compile without any island flag, run, and diff against CPython."""
    src = tmp_path / f"{name}.py"
    src.write_text(source_text, encoding="utf-8")
    binary = tmp_path / f"{name}_bin"
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(binary), cwd=tmp_path)
    assert comp.returncode == 0, comp.stderr
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(src)], capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    assert cpython.returncode == 0, cpython.stderr
    assert native.stdout == cpython.stdout
    return native.stdout, cpython.stdout


def test_pydantic_model_runs_without_any_island_flag(tmp_path: Path) -> None:
    """A model program compiles and runs in the default lane."""
    source_text = """from pydantic import BaseModel


class User(BaseModel):
    name: str
    age: int = 0


def main() -> None:
    user = User(name="alice", age=30)
    print(user.model_dump())


if __name__ == "__main__":
    main()
"""
    stdout, _ = _compare(tmp_path, source_text, name="model")
    assert stdout == "{'name': 'alice', 'age': 30}\n"


def test_pydantic_validate_and_dump_json(tmp_path: Path) -> None:
    """`model_validate` and `model_dump_json` cross the seam and match CPython."""
    source_text = """from pydantic import BaseModel


class Item(BaseModel):
    sku: str
    qty: int


def main() -> None:
    item = Item.model_validate({"sku": "a-1", "qty": 2})
    print(item.model_dump_json())
    print(Item(sku="b-2", qty=5).qty)


if __name__ == "__main__":
    main()
"""
    stdout, _ = _compare(tmp_path, source_text, name="validate")
    assert stdout == '{"sku":"a-1","qty":2}\n5\n'


def test_pydantic_nested_model(tmp_path: Path) -> None:
    """A nested model definition stays island-side and matches CPython."""
    source_text = """from pydantic import BaseModel


class Address(BaseModel):
    city: str


class Person(BaseModel):
    name: str
    address: Address


def main() -> None:
    person = Person(name="bo", address=Address(city="oslo"))
    print(person.model_dump())


if __name__ == "__main__":
    main()
"""
    stdout, _ = _compare(tmp_path, source_text, name="nested")
    assert stdout == "{'name': 'bo', 'address': {'city': 'oslo'}}\n"


def test_a_program_without_pydantic_keeps_the_no_flag_refusal(tmp_path: Path) -> None:
    """The seam's promotion is scoped: a program importing no island-defaulted
    module still fences instead of silently routing to the island."""
    src = tmp_path / "plain.py"
    src.write_text(
        """import json


def loader(text):
    return json.loads(text)


def main() -> None:
    rows: list[dict[str, int]] = loader('[{"a": 1}]')
    print(rows[0]["a"])


if __name__ == "__main__":
    main()
""",
        encoding="utf-8",
    )
    comp = _run_cli("compile", str(src), "--emit", "bin", "-o", str(tmp_path / "plain"), cwd=tmp_path)
    assert comp.returncode != 0
    assert "PS" in comp.stderr
