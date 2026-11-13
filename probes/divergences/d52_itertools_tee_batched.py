import itertools

xs: list[int] = [1, 2, 3]

left, right = itertools.tee(xs)
print(list(left))
print(list(right))

print(list(itertools.batched(xs, 2)))  # noqa: B911 - strict= is not in the lowered subset
