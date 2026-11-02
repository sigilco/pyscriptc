"""model_dump with exclusion options."""

from pydantic import BaseModel


class Rec(BaseModel):
    a: int
    b: str
    c: int = 5


r = Rec(a=1, b="x")
print(r.model_dump(exclude={"c"}))
print(r.model_dump(exclude_defaults=True))
