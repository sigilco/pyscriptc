"""Model repr shows the field values."""

from pydantic import BaseModel


class Rec(BaseModel):
    a: int
    b: str


print(repr(Rec(a=1, b="x")))
