# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP030, UP031, UP032 - the %, indexed and .format spellings are the constructs under test
"""`OrderedDict` and `defaultdict` in the value form (issue #491).

The annotation carries the key and value types: `OrderedDict[str, int]`
with `OrderedDict()`, `defaultdict[str, list[int]]` with
`defaultdict(list)`, and the int, float, str, set and dict factories. A
read through `d[k]` on a defaultdict inserts the factory's default on the
first miss; an OrderedDict round-trips insertion order. Printing either
shows CPython 3.14's repr: `OrderedDict()` / `OrderedDict({...})` and
`defaultdict(<class 'list'>, {...})`, nested too. Both sides run this
same program and the lane byte-compares stdout.
"""

from collections import OrderedDict, defaultdict


def group(xs: list[int]) -> defaultdict[int, list[int]]:
    out: defaultdict[int, list[int]] = defaultdict(list)
    for x in xs:
        out[x % 3].append(x)
    return out


def main() -> None:
    od: OrderedDict[str, int] = OrderedDict()
    print(od)
    od["b"] = 2
    od["a"] = 1
    od["c"] = 3
    od["b"] = 20
    del od["a"]
    od["a"] = 10
    for k, v in od.items():
        print(k, v)
    print(list(od.keys()), len(od), "a" in od)
    print(od)
    print([od])
    print(repr(od), "%s" % od, "%r" % od)
    print(f"{od!r}")
    print("{}".format(od), "%(b)d" % od, "{0[b]}".format(od))

    g = group([5, 1, 9, 3, 4, 2])
    for k2, vs in g.items():
        print(k2, vs)
    print(len(g), 7 in g)
    print(g[7], len(g), 7 in g)
    print(g)

    counts: defaultdict[str, int] = defaultdict(int)
    print(counts)
    for w in "a b a c b a".split():
        counts[w] += 1
    print(counts["zz"], len(counts), counts)
    print(repr(counts), "%s" % counts, "%(a)d" % counts)

    sets: defaultdict[int, set[str]] = defaultdict(set)
    sets[1].add("x")
    sets[1].add("x")
    print(sets)

    floats: defaultdict[str, float] = defaultdict(float)
    floats["a"] += 0.5
    print(floats["a"], floats["b"], floats)

    texts: defaultdict[str, str] = defaultdict(str)
    texts["a"] += "x"
    print(texts["a"] + "|" + texts["b"] + "|", texts)

    maps: defaultdict[str, dict[str, int]] = defaultdict(dict)
    maps["a"]["b"] = 1
    print(maps)

    nest: dict[str, defaultdict[str, int]] = {}
    nest["x"] = counts
    print(nest)
    onest: OrderedDict[str, OrderedDict[str, int]] = OrderedDict()
    onest["o"] = od
    print(onest)


main()
