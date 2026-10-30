"""The pin pass's caller walk is cached per pin epoch (issue #500).

The caller walk (`_call_site_locals` plus the Name-callee call scan) is a
def-independent fact of the caller, so the pin pass computes it once per
pin epoch instead of once per pending def. A pin landing mid-round bumps
the epoch: the caller whose parameter was annotated (and every caller
whose walk could read the new signature) recomputes. The order-sensitive
semantics of the pin pass are therefore preserved exactly, and these
tests hold the shapes where the epoch bump is observable: a caller that
is itself pinned and later called, a caller whose parameter the pin
annotates before another def's walk runs, and two pending defs that pin
from each other's callers.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

# Each case is a module whose pending defs pin from their call sites: the
# pin pass walks the callers, so the cached walk is the whole mechanism.
_CASES: dict[str, str] = {
    # `middle` is pinned from `probe`, and `middle`'s own body calls
    # `outer(n)`: `pick` pins from a walk of `middle`'s body, which runs
    # after `middle`'s parameter was annotated.
    "epoch": (
        "def outer(v):\n"
        "    return v\n"
        "\n"
        "def middle(n):\n"
        "    outer(n)\n"
        "    return n\n"
        "\n"
        "def probe() -> int:\n"
        "    return middle(4)\n"
    ),
    # Two pending defs, each pinning from a caller that is itself pending.
    "twodef": (
        "def shrink(v):\n"
        "    return v - 1\n"
        "\n"
        "def widen(v):\n"
        "    return v + 1\n"
        "\n"
        "def probe() -> int:\n"
        "    return shrink(widen(4))\n"
    ),
    # The call order decides which pin lands first.
    "order": (
        "def alpha(v):\n"
        "    return v + 1\n"
        "\n"
        "def beta(v):\n"
        "    return v * 2\n"
        "\n"
        "def probe() -> int:\n"
        "    return alpha(beta(3))\n"
    ),
    # The caller's own locals feed the argument peek.
    "localbind": (
        "def add(a, b):\n    return a + b\n\ndef probe() -> int:\n    x = 5\n    y = 6\n    return add(x, y)\n"
    ),
}


def test_pin_epoch_increments_when_a_pin_lands(tmp_path: Path) -> None:
    """The epoch is the cache key: it moves forward, never back."""

    from pyscriptc.lower import Lowerer

    path = write_case(tmp_path, "epochcounter.py", _CASES["twodef"])
    source = path.read_text(encoding="utf-8")
    lowerer = Lowerer(str(path), source)
    lowerer.cataloguing = False
    epochs: list[int] = []
    original = Lowerer._pin_annotation

    def spy(self: Lowerer, typ: dict[str, object]) -> ast.AST:
        epochs.append(self._pin_epoch)
        return original(self, typ)

    Lowerer._pin_annotation = spy  # type: ignore[method-assign]
    try:
        lowerer.module(ast.parse(source, filename=str(path)))
    finally:
        Lowerer._pin_annotation = original  # type: ignore[method-assign]
    assert epochs, "the pin never reached the annotation writer"
    assert epochs == sorted(epochs), "the epoch moved backwards"


def test_caller_cache_is_per_pin_pass(tmp_path: Path) -> None:
    """The caller caches reset at the start of every pin pass."""

    from pyscriptc.lower import Lowerer

    path = write_case(tmp_path, "reset.py", _CASES["twodef"])
    source = path.read_text(encoding="utf-8")
    lowerer = Lowerer(str(path), source)
    lowerer.cataloguing = False
    seen: list[int] = []
    original = Lowerer._pin_call_site_params

    def spy(self: Lowerer, functions: list[ast.FunctionDef], fences: list[object]) -> None:
        seen.append(len(self._pin_caller_locals))
        return original(self, functions, fences)  # type: ignore[arg-type]

    Lowerer._pin_call_site_params = spy  # type: ignore[method-assign]
    try:
        lowerer.module(ast.parse(source, filename=str(path)))
    finally:
        Lowerer._pin_call_site_params = original  # type: ignore[method-assign]
    assert seen, "the pin pass never ran"
    assert all(count == 0 for count in seen), f"a pin pass started with a warm cache: {seen}"


def test_pinned_cases_match_cpython(tmp_path: Path) -> None:
    """Each pinned module compiles and runs like CPython."""

    for name, source in sorted(_CASES.items()):
        path = write_case(tmp_path, f"{name}.py", source)
        got = compile_run(tmp_path, path, "probe")
        assert got.returncode == 0, (name, got.stderr)
        assert got.stdout == oracle(path, "probe"), name


def test_pinned_cases_are_stable_across_compiles(tmp_path: Path) -> None:
    """The same source compiles to the same output twice.

    The caller cache is per lowerer instance, so the second compile
    exercises the cache from cold and must not diverge.
    """

    for name, source in sorted(_CASES.items()):
        path = write_case(tmp_path, f"{name}.py", source)
        first = compile_run(tmp_path, path, "probe")
        second = compile_run(tmp_path, path, "probe")
        assert first.returncode == second.returncode == 0, (name, first.stderr, second.stderr)
        assert first.stdout == second.stdout == oracle(path, "probe"), name
