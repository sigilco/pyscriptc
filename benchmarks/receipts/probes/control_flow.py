# SPDX-License-Identifier: Apache-2.0
"""Loops, conditions, functions and exceptions.

The control-flow surface: the `for` and `while` loops, the `if`/`elif`
chain, a function with a typed signature, recursion and the `try`/
`except` shape. The exceptions are caught and printed, so the output is
the same on every target.
"""


def fib(n: int) -> int:
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


for n in range(10):
    print(n, fib(n))

total: int = 0
i: int = 0
while i < 5:
    total = total + i
    i = i + 1
print("total", total)


def classify(n: int) -> str:
    if n < 0:
        return "negative"
    elif n == 0:
        return "zero"
    else:
        return "positive"


print(classify(-1), classify(0), classify(1))


def parse(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return -1


print(parse("42"), parse("abc"))

values: list[int] = [1, 2, 3]
print(sum(values))
