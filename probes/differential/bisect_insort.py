import bisect

xs: list[int] = [1, 3, 5, 7]
print(bisect.bisect_left(xs, 4))
print(bisect.bisect_right(xs, 4))
print(bisect.bisect_left(xs, 5))

bisect.insort(xs, 4)
print(xs)
bisect.insort_left(xs, 0)
print(xs)
bisect.insort_right(xs, 8)
print(xs)

ss: list[str] = ["apple", "cherry", "mango"]
print(bisect.bisect_left(ss, "banana"))
bisect.insort(ss, "banana")
print(ss)

fs: list[float] = [0.5, 1.5, 2.5]
bisect.insort(fs, 1.75)
print(fs)
