# Divergence D61: the runtime's shortest-round-trip float formatter has no
# CPython fixed-exponent rule, so 180.0 prints as 1.8e+02. Values CPython
# also spells in exponent form byte-match.
import math

print(math.degrees(math.pi))
print(math.degrees(1.0))
print(math.sinh(1.0))
print(math.exp(20.0))
