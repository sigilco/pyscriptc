"""A field with a serialization alias."""

from pydantic import BaseModel, Field


class Alias(BaseModel):
    camel_name: str = Field(alias="camelName")


a = Alias(camelName="x")
print(a.camel_name)
