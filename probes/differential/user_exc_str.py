# SPDX-License-Identifier: Apache-2.0
# Issue #415: `str(e)` over a user exception class dispatches the class's
# own `__str__` (CPython calls `type(e).__str__(e)` first), in the caught
# and the uncaught lane alike. The class's `__init__` takes its own
# arguments (the builtin one-scalar arity does not apply), and the fields
# read back off the payload the record carries.
class ParseError(Exception):
    path: str
    lineno: int
    msg: str

    def __init__(self, path: str, lineno: int, msg: str) -> None:
        super().__init__(path, lineno, msg)
        self.path = path
        self.lineno = lineno
        self.msg = msg

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno + 1}: {self.msg}"


def main() -> None:
    try:
        raise ParseError("p.ini", 3, "boom")
    except ParseError as e:
        print(str(e))
        print(e.path, e.lineno, e.msg)


if __name__ == "__main__":
    main()
