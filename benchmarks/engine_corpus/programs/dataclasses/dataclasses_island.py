"""The dataclasses corpus program: the island module the compiler routes to the engine."""

import dataclasses


@dataclasses.dataclass
class Point:
    x: int
    y: int


@dataclasses.dataclass
class Named:
    name: str
    point: Point


def point_fields() -> str:
    return ",".join(f.name for f in dataclasses.fields(Point))


def make_named() -> str:
    n = Named("p", Point(1, 2))
    return n.name + ":" + str(n.point.x) + "," + str(n.point.y)


def as_dict() -> str:
    d = dataclasses.asdict(Point(3, 4))
    return str(d["x"]) + "," + str(d["y"])
