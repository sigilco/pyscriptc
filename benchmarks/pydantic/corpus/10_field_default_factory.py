"""Field with a default_factory."""

from pydantic import BaseModel, Field


class Bag(BaseModel):
    items: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


b = Bag()
print(b.model_dump())
