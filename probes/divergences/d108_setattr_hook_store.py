# SPDX-License-Identifier: Apache-2.0
# Divergence D108: the `__setattr__` hook's store. CPython routes every
# write through the hook, and the hook body stores through
# object.__setattr__ (the dynamic-name store: `c.y = 7` creates the
# attribute `y`). The compiled program has no instance dictionary, so the
# hook body's self-write is a plain field store: it bypasses the hook
# (the issue #436 contract, the recursion CPython would hit) and lands on
# the spelled field, not the write site's name. A write whose name is not
# a declared field therefore never creates an attribute, and a hook that
# does not store leaves the field at its zero value where CPython raises
# AttributeError on the read.
class Counter:
    count: int

    def __init__(self, count: int) -> None:
        self.count = count

    def __setattr__(self, name: str, value: int) -> None:
        print("set", name, value)
        self.count = value


c = Counter(1)
c.count = 5
print(c.count)
