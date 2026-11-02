"""Model equality compares field values."""

from pydantic import BaseModel


class Rec(BaseModel):
    a: int
    b: str


print(Rec(a=1, b="x") == Rec(a=1, b="x"))
