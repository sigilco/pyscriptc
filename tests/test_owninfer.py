"""The own-inference arm: what the local fixpoint proves and refuses.

The own lane (`infer --bridge own`) types unannotated defs from three
local evidence sources — annotations and defaults, call sites, and
returns — as a bounded fixpoint over the package's state tables. Its
soundness rule is the shared one: a parameter is pinned only when every
resolved call site agrees, and a type the subset cannot express stays a
fence. Nothing here guesses; a fact the engine cannot prove locally is
reported as unproven (`None`), never as a guess.
"""

from __future__ import annotations

from pathlib import Path

from pyscriptc.owninfer import build_own_result


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def _pins(root: Path) -> dict[tuple[str, str], str]:
    result = build_own_result(root)
    return {key: pin.kind for key, pin in result.facts.pins.items()}


def _returns(root: Path, module: str, name: str) -> str | None:
    engine = build_own_result(root).engine
    state = engine.states[module]
    facts = state.proven_returns.get(name)
    return None if facts is None else facts.runtime_repr


def test_a_literal_default_types_its_parameter(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"lib.py": "def clamp(value, limit=10):\n    return limit\n"})
    # The default alone does not pin: pinning needs every call site to
    # agree, and this tree has none. The state table still carries the
    # default's fact, so the return scan reads the default as int.
    assert _pins(root) == {}
    assert _returns(root, "lib", "clamp") == "int"


def test_agreeing_call_sites_pin_the_parameter(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "lib.py": "def shout(text):\n    return text.upper()\n",
            "__main__.py": "from lib import shout\n\nshout('a')\nshout('b')\n",
        },
    )
    assert _pins(root) == {("lib.shout", "text"): "str"}
    assert _returns(root, "lib", "shout") == "str"


def test_a_disagreeing_call_site_keeps_the_fence(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "lib.py": "def twice(value):\n    return value * 2\n",
            "__main__.py": "from lib import twice\n\ntwice(1)\ntwice(2.5)\n",
        },
    )
    assert _pins(root) == {}
    assert _returns(root, "lib", "twice") is None


def test_a_single_untypable_call_site_blocks_the_pin(tmp_path: Path) -> None:
    # The all-or-nothing rule: one call site the engine cannot type is a
    # disagreement, so even an otherwise unanimous parameter stays fenced.
    root = _tree(
        tmp_path,
        {
            "lib.py": "def widen(value):\n    return value\n",
            "__main__.py": "from lib import widen\n\nwiden(1)\nwiden(input())\n".replace("input()", "[x for x in n]"),
        },
    )
    assert _pins(root) == {}


def test_a_cross_module_pin_feeds_the_callee_return(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": "def scale(amount):\n    return amount * 10\n",
            "__main__.py": "from pkg.mod import scale\n\nscale(1)\nscale(2)\nscale(3)\n",
        },
    )
    assert _pins(root) == {("pkg.mod.scale", "amount"): "int"}
    assert _returns(root, "pkg.mod", "scale") == "int"


def test_an_annotated_parameter_seeds_but_never_pins(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"lib.py": "def keep(count: int):\n    return count + 1\n"})
    # An annotated parameter is the source's own word: it is never a pin
    # (the lowerer reads the annotation itself), but the return scan may
    # still read through it.
    assert _pins(root) == {}
    assert _returns(root, "lib", "keep") == "int"


def test_returning_defs_feed_caller_returns(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "lib.py": "def one():\n    return 1\n\n\ndef plus_one():\n    return one() + 1\n",
        },
    )
    assert _returns(root, "lib", "one") == "int"
    assert _returns(root, "lib", "plus_one") == "int"


def test_builtin_and_method_returns_type_locals(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"lib.py": "def label():\n    name = 'x'.upper()\n    return name\n"})
    assert _returns(root, "lib", "label") == "str"


def test_per_file_facts_carry_provider_own_and_position_keys(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"lib.py": "def greet():\n    return 'hi'\n"})
    result = build_own_result(root)
    facts = result.per_file[(root / "lib.py").resolve()]
    assert facts.provider == "own"
    assert facts.def_at(1, 0).return_type.runtime_repr == "str"


def test_the_fixpoint_pins_through_two_hops(tmp_path: Path) -> None:
    # A pins a typed argument, B takes a, B's caller chain pins A; the
    # folded state lets the next round prove B's return from A's pin.
    root = _tree(
        tmp_path,
        {
            "a.py": "def make(name):\n    return name.upper()\n",
            "b.py": "from a import make\n\n\ndef wrap(name):\n    return make(name)\n",
            "__main__.py": "from b import wrap\n\nwrap('x')\nwrap('y')\n",
        },
    )
    pins = _pins(root)
    assert pins.get(("a.make", "name")) == "str"
    assert pins.get(("b.wrap", "name")) == "str"
    assert _returns(root, "a", "make") == "str"
    assert _returns(root, "b", "wrap") == "str"
