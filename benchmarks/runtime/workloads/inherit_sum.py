"""Single inheritance: a child method calls a parent method."""


class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def manhattan(self) -> int:
        return self.x + self.y


class Spot(Point):
    z: int

    def __init__(self, x: int, y: int, z: int) -> None:
        self.x = x
        self.y = y
        self.z = z

    def volume(self) -> int:
        return self.manhattan() + self.z


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        spot: Spot = Spot(i, 1, 2)
        total = total + spot.volume()
        i = i + 1
    return total
