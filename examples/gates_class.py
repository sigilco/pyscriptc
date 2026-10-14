class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def total(self) -> int:
        return self.x + self.y


def origin_sum() -> int:
    point: Point = Point(3, 4)
    return point.total()
