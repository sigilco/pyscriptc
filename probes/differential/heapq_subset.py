import heapq

xs: list[int] = [5, 1, 4, 2, 3]
heapq.heapify(xs)
print(xs)
heapq.heappush(xs, 0)
print(xs)
print(heapq.heappop(xs))
print(xs)
print(heapq.heappushpop(xs, 10))
print(xs)
print(heapq.heapreplace(xs, 10))
print(xs)

print(heapq.nlargest(3, [5, 1, 4, 2, 3]))
print(heapq.nsmallest(3, [5, 1, 4, 2, 3]))
print(heapq.nlargest(0, [1, 2]))
print(heapq.nsmallest(5, [1, 2]))

empty: list[int] = []
print(heapq.heappushpop(empty, 7))

fs: list[float] = [3.5, 1.25, 2.0, 0.5]
heapq.heapify(fs)
print(fs)
print(heapq.nlargest(2, fs))

ss: list[str] = ["pear", "apple", "fig", "date", "apple"]
heapq.heapify(ss)
print(ss)
print(heapq.heappop(ss))
heapq.heappush(ss, "aa")
print(ss)
print(heapq.nsmallest(2, ss))
