"""BaseModel with plain scalar fields."""

from pydantic import BaseModel


class Point(BaseModel):
    x: int
    y: int
    label: str


p = Point(x=1, y=2, label="origin")
print(p.x, p.y, p.label)
print(p.model_dump())
