# SPDX-License-Identifier: Apache-2.0
"""List, dict and tuple shapes.

The container surface: the literal forms, the indexing, the slicing, the
comprehensions and the dict operations. The keys are inserted in a fixed
order, so the printed order is the same on every target.
"""

xs: list[int] = [5, 3, 9, 1, 7]
ys: list[int] = sorted(xs)
print(xs, ys)
print(len(xs), xs[0], xs[-1], xs[1:3])
print([x * 2 for x in xs])
print([x for x in xs if x > 4])
print(sum(xs), max(xs), min(xs))

d: dict[str, int] = {"a": 1, "b": 2}
d["c"] = 3
print(d["a"], d["c"], len(d))
print(sorted(d.keys()))
print(sorted(d.values()))
print("b" in d, "z" in d)
print({k: v * 10 for k, v in d.items()})

t: tuple[int, str] = (1, "one")
print(t[0], t[1])

nested: list[list[int]] = [[1, 2], [3, 4]]
print(nested[1][0], len(nested))
