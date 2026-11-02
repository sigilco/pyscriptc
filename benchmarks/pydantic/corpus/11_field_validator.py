"""A field_validator that uppercases a string."""

from pydantic import BaseModel, field_validator


class Name(BaseModel):
    value: str

    @field_validator("value")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


print(Name(value="ada").model_dump())
