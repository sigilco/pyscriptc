"""A model field typed by an Enum."""

from enum import Enum

from pydantic import BaseModel


class Color(Enum):
    RED = "red"
    BLUE = "blue"


class Paint(BaseModel):
    color: Color


p = Paint(color=Color.RED)
print(p.color.value)
