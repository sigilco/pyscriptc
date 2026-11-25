# SPDX-License-Identifier: Apache-2.0
"""A local first bound to `None` widens to `T | None` from its later bindings.

Issue #1370: the idiomatic "not seen yet" state (`section = None` before
the loop, `section = name` inside it) used to fence with PS1001, because a
bare `None` names no type. The local now takes the type its later bindings
prove, so the compile and the run match CPython 3.14.

The shapes are the ones iniconfig 2.3.1's `_parse.py` uses (issue #417):
a `None` first binding followed by a narrowed `str` binding, and an
`IfExp` with a `None` arm.
"""


def show(part: str | None) -> str:
    """The optional read, narrowed with `is None` as CPython does."""
    if part is None:
        return "None"
    return part


def parse_lines(lines: list[str]) -> list[tuple[str | None, str | None]]:
    """The `section = None` shape: the None first, the narrowed str later."""
    result: list[tuple[str | None, str | None]] = []
    section = None
    for line in lines:
        if line.startswith("["):
            section = line[1:-1]
            result.append((section, None))
        elif line.startswith(" ") and section is not None:
            result.append((section, line.strip()))
        elif section is not None:
            result.append((section, None))
    return result


def first_value(values: list[str]) -> str | None:
    """The `IfExp` shape: one arm is `None`, the other proves the type."""
    value = values[-1] if values else None
    if value is None:
        return None
    return value


def widen_from_two_bindings(flag: bool) -> str | None:
    """Two later bindings of the same type agree on the type."""
    section = None
    if flag:
        section = "a"
    else:
        section = "b"
    return section


def main() -> None:
    for section, value in parse_lines(["[top]", "  key = 1", "[other]"]):
        print(show(section), show(value))
    print(show(first_value([])))
    print(show(first_value(["x"])))
    print(show(widen_from_two_bindings(True)))
    print(show(widen_from_two_bindings(False)))


if __name__ == "__main__":
    main()
