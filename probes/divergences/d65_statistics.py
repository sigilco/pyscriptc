# Divergence D64: the statistics subset runs double arithmetic for the
# float-valued seams. CPython's statistics module converts every number
# to an exact rational and converts the result back to float at the end,
# so a minority of datasets round differently here: stdev/pstdev (an
# exact 109-bit correctly-rounded integer sqrt is out of the i64 reach,
# one ulp of difference), and the mean/median/variance family over
# list[int] returns float where CPython returns int (mean and median of
# an exactly divisible int dataset, a variance whose exact fraction is
# integral): the value is equal, the repr is not.
import statistics

xs: list[int] = [1, 2, 3, 4, 4]
print(statistics.median(xs))
print(statistics.mean(xs))
print(statistics.variance(xs))
print(statistics.stdev(xs))
print(statistics.pstdev(xs))
