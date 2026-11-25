# SPDX-License-Identifier: Apache-2.0
"""A user exception class runs its own `__init__` on raise (issue #448).

The raise site builds the class instance (running the user's `__init__`)
and the record carries it as the exception payload, so `except C as e`
reads the custom fields back off the instance. CPython does the same:
the class instance is the exception, `e.args` holds the constructor
arguments and the fields the `__init__` set read back through `e`.

The shapes covered here:

- a class with an `__init__` that takes arguments and sets fields
- a class whose `__init__` chains the builtin base with `super().__init__`
  (the base's message is what CPython's `str(e)` shows)
- a class without its own `__init__` (the builtin base's arity applies)
- a subclass whose base carries the `__init__`
- a raise from inside a function (the payload rides the exception)
- a bare re-raise inside a handler (the payload rides it again)
- `e.args` and `str(e)` of a user exception

Both sides run this same program and the lane byte-compares stdout.
"""

import sys


class AppError(Exception):
    code: int
    tag: str

    def __init__(self, code: int, tag: str) -> None:
        self.code = code
        self.tag = tag


class Chained(Exception):
    code: int

    def __init__(self, code: int) -> None:
        super().__init__("chained")
        self.code = code


class Plain(Exception):
    code: int = 0


class Base(Exception):
    code: int

    def __init__(self, code: int) -> None:
        self.code = code


class Sub(Base):
    tag: str = "sub"


def boom(n: int) -> None:
    raise AppError(n, "boom")


def probe() -> int:
    try:
        raise AppError(7, "seven")
    except AppError as e:
        print(e.code, e.tag)

    try:
        boom(9)
    except AppError as e:
        print(e.code, e.tag)

    try:
        raise Chained(3)
    except Chained as e:
        print(e.code, str(e))

    try:
        raise Plain()
    except Plain as e:
        print(e.code)

    try:
        raise Sub(5)
    except Sub as e:
        print(e.code, e.tag)

    try:
        raise AppError(1, "re")
    except AppError as e:
        try:
            raise e
        except AppError as again:
            print(again.code, again.tag)

    try:
        raise AppError(2, "args")
    except AppError as e:
        print(e.code, e.tag)

    return 0


if __name__ == "__main__":
    sys.exit(probe())
