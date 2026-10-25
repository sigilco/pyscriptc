# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "attrs>=23.0.0",
# ]
# ///
from __future__ import annotations

import attrs


@attrs.define
class Point:
    x: int
    y: int

    def distance_sq(self) -> int:
        return self.x * self.x + self.y * self.y


@attrs.define
class Rectangle:
    top_left: Point
    bottom_right: Point

    def area(self) -> int:
        width: int = self.bottom_right.x - self.top_left.x
        height: int = self.bottom_right.y - self.top_left.y
        return width * height


def main() -> None:
    p1: Point = Point(0, 0)
    p2: Point = Point(10, 5)
    rect: Rectangle = Rectangle(p1, p2)
    print("P1 dist sq:", p1.distance_sq())
    print("P2 dist sq:", p2.distance_sq())
    print("Area:", rect.area())
    d: dict[str, int] = attrs.asdict(p2)
    print("P2 asdict:", sorted(d.items()))


if __name__ == "__main__":
    main()
