"""Issue #1307: the `@island` marker of the shadow annotation module.

`pyscriptc._annotations.island` marks one definition the compiler routes to
the island on purpose. The marker is per definition: it routes that one
definition and leaves every sibling on its own plane. The compile and
`assess` name the deliberate routing instead of a fence.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from pyscriptc.boxed import boxed_definitions, island_marked_definitions
from pyscriptc.lower import lower_source

ROOT = Path(__file__).resolve().parents[1]

MARKED = """
from pyscriptc._annotations import island


@island
def f(x: int) -> int:
    return x + 1


def probe() -> int:
    return f(7)
"""

MARKED_ALIAS = """
import pyscriptc._annotations as ann


@ann.island
def f(x: int) -> int:
    return x + 1
"""

MARKED_DOTTED = """
import pyscriptc._annotations


@pyscriptc._annotations.island
def f(x: int) -> int:
    return x + 1
"""

MARKED_FROM_ALIAS = """
from pyscriptc._annotations import island as isl


@isl
def f(x: int) -> int:
    return x + 1
"""


def _marked_names(source: str) -> list[str]:
    return island_marked_definitions(ast.parse(source))


def test_marker_is_found_in_every_spelling() -> None:
    assert _marked_names(MARKED) == ["f"]
    assert _marked_names(MARKED_ALIAS) == ["f"]
    assert _marked_names(MARKED_DOTTED) == ["f"]
    assert _marked_names(MARKED_FROM_ALIAS) == ["f"]


def test_marker_is_not_found_on_an_undecorated_definition() -> None:
    source = """
from pyscriptc._annotations import island


def f(x: int) -> int:
    return x + 1
"""
    assert _marked_names(source) == []


def test_a_local_decorator_named_island_is_not_the_marker() -> None:
    source = """
def island(fn):
    return fn


@island
def f(x: int) -> int:
    return x + 1
"""
    assert _marked_names(source) == []


def test_specialize_is_not_the_marker() -> None:
    source = """
from pyscriptc._annotations import specialize


@specialize
def f(x: int) -> int:
    return x + 1
"""
    assert _marked_names(source) == []


def test_the_marker_routes_only_its_definition() -> None:
    with boxed_definitions(True):
        module = lower_source(MARKED, "case.py")
    planes = module["fallback"]["functions"]
    assert planes["f"] == "island"
    assert planes["probe"] == "native"


def test_the_marker_carries_the_demotion_note() -> None:
    with boxed_definitions(True):
        module = lower_source(MARKED, "case.py")
    notes = module.get("_notes", [])
    assert any("marked @island" in note and "f" in note for note in notes), notes
    assert module["fallback"]["demotedBy"]["f"] == "marked @island"
    assert module["fallback"]["islandMarked"] == ["f"]


def test_an_undecorated_module_is_untouched() -> None:
    plain = """
def f(x: int) -> int:
    return x + 1


def probe() -> int:
    return f(7)
"""
    with boxed_definitions(True):
        module = lower_source(plain, "case.py")
    assert module["fences"] == []
    assert not module.get("_notes")
    assert module["fallback"]["functions"] == {"f": "native", "probe": "native"}
    assert module["fallback"]["islandMarked"] == []


def test_a_marked_class_rides_the_island() -> None:
    source = """
from pyscriptc._annotations import island


@island
class C:
    def value(self) -> int:
        return 7
"""
    with boxed_definitions(True):
        module = lower_source(source, "case.py")
    assert module["fallback"]["classes"]["C"] == "island"
    assert any("marked @island" in note for note in module.get("_notes", [])), module.get("_notes")


def test_the_marker_does_not_fence_the_definition() -> None:
    with boxed_definitions(True):
        module = lower_source(MARKED, "case.py")
    assert module["fences"] == []


def test_assess_names_the_deliberate_routing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from pyscriptc import cli

    path = tmp_path / "marked.py"
    path.write_text(MARKED, encoding="utf-8")
    code = cli.main(["assess", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "f: island (demoted by marked @island)" in out


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_marked_program_matches_cpython(tmp_path: Path) -> None:
    source = """
from pyscriptc._annotations import island


@island
def marked(x: int) -> int:
    total: int = 0
    for i in range(x):
        total += i
    return total


def plain(x: int) -> int:
    total: int = 0
    for i in range(x):
        total += i
    return total


@island
class Marked:
    def value(self) -> int:
        return 7


class Plain:
    def value(self) -> int:
        return 7


print(marked(5))
print(plain(5))
print(Marked().value())
print(Plain().value())
"""
    path = tmp_path / "marked.py"
    path.write_text(source, encoding="utf-8")
    binary = tmp_path / "marked_bin"
    comp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--fallback=cpython",
            "--allow-host-libpython",
            "--emit",
            "bin",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert comp.returncode == 0, comp.stderr
    assert "marked @island" in comp.stderr, comp.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout == cpython.stdout
    assert run.stdout == "10\n10\n7\n7\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_the_differential_probe_matches_cpython(tmp_path: Path) -> None:
    probe = ROOT / "probes" / "differential" / "island_marker.py"
    binary = tmp_path / "probe_bin"
    comp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(probe),
            "--fallback=cpython",
            "--allow-host-libpython",
            "--emit",
            "bin",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    cpython = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert run.returncode == cpython.returncode
    assert run.stdout == cpython.stdout
    assert run.stderr == cpython.stderr
