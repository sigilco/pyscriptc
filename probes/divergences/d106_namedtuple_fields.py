# SPDX-License-Identifier: Apache-2.0
from typing import NamedTuple


class Point(NamedTuple):
    x: int
    y: str


p = Point(1, "a")
print(p._fields)
print(p._asdict())
print(p._replace(y="z"))
