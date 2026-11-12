import heapq

# The issue's scope names tuples of orderable scalars as heap elements, but
# ordering comparisons on tuple values are not in the lowered surface yet,
# so a tuple heapq call stops at a fence where CPython orders the tuples.
xs: list[tuple[int, int]] = [(2, 1), (1, 2)]
heapq.heapify(xs)
print(xs)
