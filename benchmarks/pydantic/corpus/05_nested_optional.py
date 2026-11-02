"""Optional nested model."""

from pydantic import BaseModel


class Inner(BaseModel):
    v: int


class Outer(BaseModel):
    inner: Inner | None = None


print(Outer().model_dump())
print(Outer(inner=Inner(v=2)).model_dump())
