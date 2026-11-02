"""model_fields_set reports the explicitly passed fields."""

from pydantic import BaseModel


class Rec(BaseModel):
    a: int
    b: int = 2


print(sorted(Rec(a=1).model_fields_set))
