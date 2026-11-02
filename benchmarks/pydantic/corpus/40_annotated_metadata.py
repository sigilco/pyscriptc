"""Annotated metadata on a field (title/description) kept through the model."""

from typing import Annotated

from pydantic import BaseModel, Field


class Doc(BaseModel):
    name: Annotated[str, Field(description="the name", title="Name")]


d = Doc(name="x")
print(d.name)
