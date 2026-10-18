"""Set algebra: `|`, `&`, `-`, `^` and the mutating/pure set methods."""


def algebra() -> int:
    a: set[int] = {1, 2, 3}
    b: set[int] = {3, 4}
    u: set[int] = a | b
    if len(u) != 4:
        return 1
    i: set[int] = a & b
    if len(i) != 1 or 3 not in i:
        return 2
    d: set[int] = a - b
    if len(d) != 2 or 3 in d:
        return 3
    x: set[int] = a ^ b
    if len(x) != 3 or 3 in x:
        return 4
    s: set[str] = {"a", "b"}
    t: set[str] = {"b", "c"}
    if len(s | t) != 3 or len(s & t) != 1:
        return 5
    if len(s - t) != 1 or "b" in s - t:
        return 6
    return 0


def methods() -> int:
    a: set[int] = {1, 2, 3}
    b: set[int] = {3, 4}
    a.discard(2)
    if 2 in a:
        return 1
    a.discard(99)
    if len(a) != 2:
        return 2
    u: set[int] = a.union(b)
    if len(u) != 3 or 4 not in u:
        return 3
    i: set[int] = a.intersection(b)
    if len(i) != 1 or 3 not in i:
        return 4
    try:
        a.remove(99)
        return 5
    except KeyError:
        pass
    a.remove(3)
    if 3 in a:
        return 6
    words: set[str] = {"x", "y"}
    merged: set[str] = words.union({"z"})
    if len(merged) != 3:
        return 7
    return 0
