import itertools

xs: list[int] = [1, 2, 3, 4]

print(list(itertools.dropwhile(lambda v: v < 3, xs)))
print(list(itertools.compress(xs, [1, 0, 1, 0])))
print(list(itertools.permutations(xs, 2)))
print(list(itertools.pairwise(xs)))
print(list(itertools.batched(xs, 3)))  # noqa: B911 - strict= is not in the lowered subset

left, right = itertools.tee(xs)
print(list(left))
print(list(right))
