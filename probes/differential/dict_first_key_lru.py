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
