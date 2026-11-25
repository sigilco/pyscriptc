# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043, UP045, UP006 - the typing.Iterator / typing.Generator spellings are the constructs under test
"""G2b differential probe: generator throw() and close() (issue #438).

`throw(E)` resumes the frame with the exception pending, so the
generator's own `except` handler sees the exception and execution
continues inside the generator (CPython's outcome, and the one the
G2a surface could not reach). `close()` is `throw(GeneratorExit)` with
CPython's two rules: the pending `finally` runs and an uncaught
GeneratorExit is swallowed, while a generator that catches GeneratorExit
and yields again raises RuntimeError.

The variables are annotated `Generator[...]` (the protocol that carries
`throw` and `close`): the checker preflight's `Iterator` protocol has no
`throw`/`close` rows, because adding them there broke every structural
`__iter__`/`__next__` class (issue #340).

Both sides run this same program and the lane byte-compares stdout,
stderr and the exit code.
"""

from typing import Generator


def catcher() -> Generator[int, None, None]:
    try:
        yield 1
        yield 2
    except ValueError:
        print("caught inside")
        yield 3
        yield 4


def cleaner() -> Generator[int, None, None]:
    try:
        yield 1
    finally:
        print("cleanup")


def stubborn() -> Generator[int, None, None]:
    try:
        yield 1
    except GeneratorExit:
        print("caught GeneratorExit")
        yield 2


def plain() -> Generator[int, None, None]:
    yield 1
    yield 2


def main() -> None:
    # The handler inside the generator runs and the generator keeps
    # going: `throw` answers the value the generator yielded after it
    # caught the exception.
    g = catcher()
    print("first", next(g))
    print("thrown", g.throw(ValueError))
    print("after", next(g))

    # An exception no handler matches propagates out of the call site.
    h = plain()
    print("h0", next(h))
    try:
        h.throw(KeyError)
    except KeyError:
        print("KeyError at the call site")

    # `close()` runs the pending `finally` and swallows the
    # GeneratorExit.
    c = cleaner()
    print("c0", next(c))
    c.close()
    print("closed")

    # A generator that catches GeneratorExit and yields again raises
    # RuntimeError.
    s = stubborn()
    print("s0", next(s))
    try:
        s.close()
    except RuntimeError as e:
        print("RuntimeError", e)

    # `throw` on a generator that never ran raises the thrown exception
    # at the call site.
    f = plain()
    try:
        f.throw(ValueError)
    except ValueError:
        print("ValueError at the call site")

    # `close` on a generator that never ran is a no-op.
    f2 = plain()
    f2.close()
    print("closed fresh")


main()
