# SPDX-License-Identifier: Apache-2.0
"""The inference gate: an unannotated parameter still lowers natively.

Issue #1305 (the gate, 2026-11-27): the compile lowers the tree once with
no bridge and runs the engine only when a fence asks for it. This probe
pins the feature the gate must not eat: a parameter whose type the call
sites agree on still lowers natively, and the program prints what CPython
prints.

The shapes are the ones the gate's own tests use: a helper with an
unannotated parameter called from one module with one agreeing type, and
a helper whose call sites disagree (which stays fenced, so the gate has
to distinguish the two).
"""


def repeat(text, times=2):
    """The unannotated parameter: the call sites agree, so the pin proves it."""
    out = ""
    for _ in range(times):
        out = out + text
    return out


def mix(left, right):
    """The disagreeing parameter: the call sites do not agree, so it stays fenced."""
    return left + right


def main() -> None:
    print(repeat("ab"))
    print(repeat("cd", 3))
    print(mix("ef", "gh"))
    print(len(repeat("ij")))


if __name__ == "__main__":
    main()
