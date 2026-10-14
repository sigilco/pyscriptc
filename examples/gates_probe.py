"""One function that walks the lowered gates. Return 0 when every check holds."""

import math
from math import floor, pi


class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def total(self) -> int:
        return self.x + self.y


def early() -> int:
    try:
        return 5
    except ValueError:
        return 6


def probe() -> int:
    if floor(pi) != 3:
        return 1
    if math.floor(-1.2) != -2:
        return 2
    if math.ceil(1.2) != 2:
        return 3
    if math.trunc(-1.9) != -1:
        return 4
    if math.hypot(3.0, 4.0) != 5.0:
        return 5
    if math.sqrt(4) != 2.0:
        return 6
    if math.fabs(-2.5) != 2.5:
        return 7
    if math.sin(0.0) != 0.0:
        return 8
    if math.cos(0.0) != 1.0:
        return 8
    if math.tan(0.0) != 0.0:
        return 8
    if math.exp(0.0) != 1.0:
        return 9
    if math.log(1.0) != 0.0:
        return 9
    if math.log2(8.0) != 3.0:
        return 9
    if math.log10(100.0) != 2.0:
        return 9
    if math.pow(2.0, 3.0) != 8.0:
        return 10
    if math.pow(-2.0, 3.0) != -8.0:
        return 10
    if not math.isnan(math.nan):
        return 11
    if not math.isnan(math.sqrt(math.nan)):
        return 11
    if math.nan == math.nan:
        return 12
    if not (math.nan != math.nan):
        return 12
    if not math.isinf(math.inf):
        return 13
    if not math.isinf(-math.inf):
        return 13
    if pi < 3.14:
        return 14
    if pi > 3.15:
        return 14
    if math.e < 2.7:
        return 14
    if math.e > 2.8:
        return 14
    if math.tau < 6.2:
        return 14
    if int(1.9) != 1:
        return 15
    if int(-1.9) != -1:
        return 15
    if float(2) != 2.0:
        return 15
    text: str = "  Ab  "
    if text.strip() != "Ab":
        return 16
    if text.lstrip() != "Ab  ":
        return 16
    if text.rstrip() != "  Ab":
        return 16
    if text.strip().lower() != "ab":
        return 17
    if text.strip().upper() != "AB":
        return 17
    if not text.strip().lower().startswith("ab"):
        return 18
    if not text.strip().lower().endswith("ab"):
        return 18
    if "ab".replace("", "x") != "xaxbx":
        return 19
    if "ab".find("") != 0:
        return 19
    if "a" >= "b":
        return 20
    if "b" not in "ab":
        return 20
    if "z" in "ab":
        return 20
    word: str = "ab"
    if word[-1] != "b":
        return 21
    if word[0] != "a":
        return 21
    raw: bytes = b" Ab "
    if raw.strip().lower() != b"ab":
        return 22
    if raw[1] != 65:
        return 22
    if b"ab"[-1] != 98:
        return 22
    if b"\xff"[0] != 255:
        return 23
    if b"a" + b"b" != b"ab":
        return 23
    if b"b" not in b"abc":
        return 23
    xs: list[int] = [1, 2, 3]
    ys: list[int] = xs
    ys[0] = 9
    if xs[0] != 9:
        return 24
    if xs[-1] != 3:
        return 24
    if len(xs) != 3:
        return 24
    acc: int = 0
    for item in xs:
        acc = acc + item
    if acc != 14:
        return 25
    empty: list[int] = []
    if len(empty) != 0:
        return 26
    if 9 not in xs:
        return 26
    if 4 in xs:
        return 26
    down: int = 0
    for i in range(3, 0, -1):
        down = down + i
    up: int = 0
    for i in range(4):
        up = up + i
    if down != 6:
        return 27
    if up != 6:
        return 27
    table: dict[str, int] = {"a": 1, "b": 2}
    if table["b"] != 2:
        return 28
    if "a" not in table:
        return 28
    if len(table) != 2:
        return 28
    table["a"] = 7
    if table["a"] != 7:
        return 29
    nums: dict[int, int] = {1: 10}
    if nums[1] != 10:
        return 30
    if 1 not in nums:
        return 30
    blank: dict[str, int] = {}
    if len(blank) != 0:
        return 31
    words: list[str] = ["aa", "bb"]
    if "bb" not in words:
        return 32
    point: Point = Point(3, 4)
    other: Point = Point(1, 2)
    if point.total() != 7:
        return 33
    if other.total() != 3:
        return 33
    point.x = 10
    if point.total() != 14:
        return 34
    if other.total() != 3:
        return 34
    points: list[Point] = [Point(1, 2), Point(3, 4)]
    points[0].x = 10
    if points[0].total() != 12:
        return 35
    if points[1].total() != 7:
        return 35
    if early() != 5:
        return 36
    caught: int = 0
    try:
        raise ValueError("later")
    except ValueError:
        caught = 1
    if caught != 1:
        return 37
    wild: int = 0
    try:
        raise IndexError
    except Exception:
        wild = 1
    if wild != 1:
        return 38
    both: int = 0
    try:
        raise KeyError("k")
    except ValueError, KeyError:
        both = 1
    if both != 1:
        return 39
    n: int = 0
    k: int = 0
    while k < 2:
        try:
            if k == 0:
                raise ValueError("x")
            n = n + k
        except ValueError:
            n = n + 10
        k = k + 1
    if n != 11:
        return 40
    try:
        raise ValueError("after")
    except ValueError:
        n = n + 1
    if n != 12:
        return 41
    nested: int = 0
    try:
        try:
            raise ValueError("in")
        except IndexError:
            nested = 1
    except ValueError:
        nested = 3
    if nested != 3:
        return 42
    flag: int = 0
    try:
        raise ValueError("v")
    except ValueError:
        flag = 1
    except Exception:
        flag = 2
    if flag != 1:
        return 43
    held: int = 7
    for held in range(0):
        held = 1
    if held != 7:
        return 44
    floats: list[float] = [1.5]
    if 1.5 not in floats:
        return 45
    return 0
