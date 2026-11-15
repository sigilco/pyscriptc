"""Covariant nominal subclass returns (#1243).

A slot annotated with a class accepts a value whose class is a nominal
subclass of it when the subclass adds no instance fields. The slot keeps
the concrete class: dispatch is static (`cls.{owner}.{method}`), so a
`Base`-typed slot would call the base's method on a subclass value and
answer differently from CPython. A subclass that adds a field keeps the
PS1003 fence and the fence names the field; a def whose returns split
over two subclasses proves no concrete class and keeps the fence at the
return sites (the signature could not widen, so the static call would
mis-dispatch). An argument annotated with the base still fences: the
callee's body would dispatch on the annotation's class.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import compile_run, run_cli, write_case

from pyscriptc.lower import lower_source


def _fences(source: str) -> list[dict[str, str]]:
    return lower_source(source, "case.py")["fences"]


def _fences_of(*parts: str) -> list[dict[str, str]]:
    return _fences("".join(parts))


_FACTORY = (
    "class Base:\n"
    "    def who(self) -> str:\n"
    '        return "base"\n'
    "\n"
    "\n"
    "class Sub(Base):\n"
    "    def who(self) -> str:\n"
    '        return "sub"\n'
    "\n"
    "\n"
    "def make() -> Base:\n"
    "    return Sub()\n"
    "\n"
    "\n"
    "def probe() -> str:\n"
    "    b: Base = make()\n"
    "    return b.who()\n"
)


def test_factory_returning_subclass_lowers_and_answers_the_subclass() -> None:
    module = lower_source(_FACTORY, "case.py")
    assert module["fences"] == []


def test_repro_runs_and_matches_cpython(tmp_path: Path) -> None:
    source = write_case(tmp_path, "cov.py", _FACTORY + "\n\nprint(probe())\n")
    native = run_cli("compile", str(source), "--run", "-o", str(tmp_path / "cov"))
    assert native.returncode == 0, native.stderr
    assert native.stdout == "sub\n"


def test_direct_construction_keeps_the_concrete_slot() -> None:
    module = lower_source(
        "class Sender:\n"
        "    def send(self, msg: str) -> str:\n"
        '        return "sent:" + msg\n'
        "\n"
        "\n"
        "class Logging(Sender):\n"
        "    def send(self, msg: str) -> str:\n"
        '        return "log:" + msg\n'
        "\n"
        "\n"
        "def probe() -> str:\n"
        "    s: Sender = Logging()\n"
        '    return s.send("x")\n',
    )
    assert module["fences"] == []


def test_subclass_with_a_field_fences_and_names_it() -> None:
    got = _fences(
        "class Base:\n"
        "    def who(self) -> str:\n"
        '        return "base"\n'
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    extra: int\n"
        "\n"
        "    def __init__(self, extra: int) -> None:\n"
        "        self.extra = extra\n"
        "\n"
        "    def who(self) -> str:\n"
        '        return "sub"\n'
        "\n"
        "\n"
        "def make() -> Base:\n"
        "    return Sub(1)\n",
    )
    assert got, "the field-bearing subclass must fence"
    assert got[0]["code"] == "PS1003"
    assert "extra" in got[0]["message"]


def test_two_subclasses_prove_nothing_and_fence_at_the_returns() -> None:
    got = _fences(
        "class Base:\n"
        "    def who(self) -> str:\n"
        '        return "base"\n'
        "\n"
        "\n"
        "class Sub(Base):\n"
        "    def who(self) -> str:\n"
        '        return "sub"\n'
        "\n"
        "\n"
        "class Other(Base):\n"
        "    def who(self) -> str:\n"
        '        return "other"\n'
        "\n"
        "\n"
        "def make(flag: bool) -> Base:\n"
        "    if flag:\n"
        "        return Sub()\n"
        "    return Other()\n",
    )
    assert got, "the split-factory def must fence"
    assert {fence["code"] for fence in got} == {"PS1003"}


def test_base_annotated_argument_still_fences() -> None:
    got = _fences(
        "class Sender:\n"
        "    def send(self, msg: str) -> str:\n"
        '        return "sent:" + msg\n'
        "\n"
        "\n"
        "class Logging(Sender):\n"
        "    def send(self, msg: str) -> str:\n"
        '        return "log:" + msg\n'
        "\n"
        "\n"
        "def deliver(s: Sender, msg: str) -> str:\n"
        "    return s.send(msg)\n"
        "\n"
        "\n"
        "def probe() -> str:\n"
        '    return deliver(Logging(), "x")\n',
    )
    assert got, "the base-annotated parameter must keep its fence"
    assert got[0]["code"] == "PS1003"


def test_covariant_compiles_and_runs_against_the_oracle(tmp_path: Path) -> None:
    source = write_case(tmp_path, "cov.py", _FACTORY)
    done = compile_run(tmp_path, source, "probe")
    assert done.returncode == 0, done.stderr + done.stdout
    assert done.stdout == "sub\n"
