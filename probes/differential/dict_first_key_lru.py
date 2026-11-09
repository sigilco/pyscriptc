# Issue #1168: `next(iter(d))` on a mutated dict is the LRU eviction peek.
# The head ABI must read position 0 of the live rows in insertion order,
# never materialize the key list, and raise StopIteration on an empty map.
d: dict[int, int] = {}
for i in range(8):
    d[i] = i * i
first = next(iter(d))
d.pop(first)
second = next(iter(d))
# pop-reinsert moves 3 to the back
v = d.pop(3)
d[3] = v
third = next(iter(d))
print(first, second, third, len(d))
empty: dict[int, int] = {}
try:
    next(iter(empty))
    print("no-raise")
except StopIteration:
    print("stop")
sd: dict[str, int] = {"a": 1, "b": 2}
print(next(iter(sd)), len(sd))
sd.pop("a")
print(next(iter(sd)))
# pop+reinsert churn with interleaved head peeks: the order cache must
# stay in sync across deletes (issue #1168 runtime path).
d2: dict[int, int] = {}
seed = 11
acc = 0
for i in range(4000):
    seed = (seed * 1103515245 + 12345) % 2147483648
    k = (seed >> 8) % 64
    if k in d2:
        v = d2.pop(k)
        d2[k] = v
    else:
        if len(d2) >= 32:
            d2.pop(next(iter(d2)))
        d2[k] = i
    if i % 13 == 0:
        acc += next(iter(d2))
print(acc, len(d2))
