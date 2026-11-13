# Issue #486: the statistics subset over list[int] and list[float].
#
# The byte-comparable surface: the median family, mode, the variance
# family and quantiles over int data, where the subset's exact integer
# arithmetic matches CPython's exact-rational path value for value.
#
# The seams that are recorded in divergence 65 (D65) instead of probed
# here: mean/median/variance over list[int] return float where CPython
# returns int (equal value, different repr), stdev/pstdev differ by one
# ulp from CPython's correctly-rounded integer sqrt, and the float data
# path folds in double arithmetic.
import statistics

xs: list[int] = [4, 1, 3, 1, 5]
ys: list[int] = [1, 2, 3, 4, 5, 6]
zs: list[float] = [1.5, 2.5, 3.5, 4.5, 5.5]

print(statistics.median_low(xs), statistics.median_high(xs))
print(statistics.median_low(ys), statistics.median_high(ys))
print(statistics.median_low(zs), statistics.median_high(zs))
print(statistics.mode(xs))
print(statistics.pvariance(ys), statistics.variance(ys))
print(statistics.pstdev(ys))

qs: list[float] = statistics.quantiles(ys)
print(qs[0], qs[1], qs[2])
qs2: list[float] = statistics.quantiles(ys, n=2)
print(qs2[0])
qs8: list[float] = statistics.quantiles(ys, n=8)
print(qs8[0], qs8[3], qs8[6])

# The subset leaves the caller's list alone, like every CPython
# statistics function.
print(xs, ys, zs)
