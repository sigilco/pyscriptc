"""model_copy with an update dict."""

from pydantic import BaseModel


class Rec(BaseModel):
    a: int
    b: str


r = Rec(a=1, b="x")
r2 = r.model_copy(update={"a": 2})
print(r2.a, r2.b)
