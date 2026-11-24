# SPDX-License-Identifier: Apache-2.0
# Divergence D97: a fixed-shape tuple default and a fixed-shape tuple class
# attribute materialize a fresh record per read, where CPython holds one tuple
# object. The values are element for element equal, so only the identity
# comparison tells the two apart: CPython prints True, the lowered program
# prints False.
from dataclasses import dataclass


class Pair:
    pair: tuple[int, int] = (1, 2)


@dataclass
class Field:
    pair: tuple[int, int] = (1, 2)


print(Pair.pair is Pair.pair)
print(Pair().pair is Pair().pair)
print(Field().pair is Field().pair)
print(Pair.pair == (1, 2), Field().pair == (1, 2))
