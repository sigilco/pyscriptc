# SPDX-License-Identifier: Apache-2.0
# Divergence 73 (issue #413): a multi-arm union erases to its first arm.
# CPython keeps the union at run time only through annotations, which it
# does not evaluate, so the program runs there; here the slot carries the
# first arm's layout, so a call that passes a later arm's value stops the
# compile with PS1003.
def take(x: int | str) -> int:
    return 1


def run() -> int:
    print(take("five"))
    return 0


run()
