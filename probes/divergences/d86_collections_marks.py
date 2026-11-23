# SPDX-License-Identifier: Apache-2.0
from collections import OrderedDict, defaultdict


def show(d: dict[str, int]) -> None:
    print(d)


def read(d: dict[str, int], k: str) -> int:
    return d[k]


def main() -> None:
    od: OrderedDict[str, int] = OrderedDict()
    od["b"] = 2
    show(od)
    dd: defaultdict[str, int] = defaultdict(int)
    dd["x"] = 1
    show(dd)
    print(read(dd, "zz"))


main()
