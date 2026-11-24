# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043, UP045 - the typing.Generator spelling is the construct under test (the frame's send method exists only when the annotation names a send type)
"""G2a differential probe: yield as an expression, driven with send().

A consumer pumps the generator with send(None), send("ab"), send("cde")
and then drains it: the closing send(None) crosses `break`, so the frame
finishes with a None-typed return value and the caller's send raises
StopIteration. The compiled program must print what CPython prints,
byte for byte, including the traceback-free exhaustion path.
"""

from typing import Generator, Optional


def echo() -> Generator[int, Optional[str], None]:
    total: int = 0
    while True:
        word: Optional[str] = yield total
        if word is None:
            break
        total = total + len(word)
    return None


def main() -> None:
    g = echo()
    out: str = ""
    out += str(g.send(None))
    out += " " + str(g.send("ab"))
    out += " " + str(g.send("cde"))
    try:
        g.send(None)
    except StopIteration:
        out += " done"
    print(out)


main()
