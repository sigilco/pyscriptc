"""A plain (non-discriminated) union field."""

from pydantic import BaseModel


class Mixed(BaseModel):
    value: int | str


print(Mixed(value=1).model_dump())
print(Mixed(value="x").model_dump())
