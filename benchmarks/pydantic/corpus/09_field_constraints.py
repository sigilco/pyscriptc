"""Field with numeric and length constraints."""

from pydantic import BaseModel, Field


class Bounded(BaseModel):
    count: int = Field(ge=0, le=10)
    name: str = Field(min_length=1, max_length=5)


b = Bounded(count=3, name="abc")
print(b.model_dump())
